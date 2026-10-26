"""EventBridge: default bus, rules with full content-filter patterns, targets invoked with the rule's
IAM role, retries with a dead-letter queue, and at-least-once delivery.

Delivery is queued and drained in order (`drain()`); with `duplicate_rate > 0` some events are delivered
twice, as EventBridge may do. Consumers must be idempotent; the tests hold the parser to that.
"""
from __future__ import annotations

import random
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

from .iam import ACCOUNT, REGION


# --- pattern matching ------------------------------------------------------------------------------
def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _wild(p: str, v: str) -> bool:
    import re
    return re.fullmatch("".join(".*" if c == "*" else re.escape(c) for c in p), v, re.DOTALL) is not None


def _value_matches(matcher, value) -> bool:
    if not isinstance(matcher, dict):                           # exact (string / number / null / bool)
        return matcher == value
    if "prefix" in matcher:
        m = matcher["prefix"]
        if isinstance(m, dict):                                 # {"prefix": {"equals-ignore-case": ...}}
            return isinstance(value, str) and value.lower().startswith(m["equals-ignore-case"].lower())
        return isinstance(value, str) and value.startswith(m)
    if "suffix" in matcher:
        m = matcher["suffix"]
        if isinstance(m, dict):
            return isinstance(value, str) and value.lower().endswith(m["equals-ignore-case"].lower())
        return isinstance(value, str) and value.endswith(m)
    if "equals-ignore-case" in matcher:
        return isinstance(value, str) and value.lower() == matcher["equals-ignore-case"].lower()
    if "wildcard" in matcher:
        return isinstance(value, str) and _wild(matcher["wildcard"], value)
    if "anything-but" in matcher:
        ab = matcher["anything-but"]
        if isinstance(ab, dict):
            if "prefix" in ab:
                return isinstance(value, str) and not value.startswith(ab["prefix"])
            if "suffix" in ab:
                return isinstance(value, str) and not value.endswith(ab["suffix"])
            if "wildcard" in ab:
                return isinstance(value, str) and not _wild(ab["wildcard"], value)
            if "equals-ignore-case" in ab:
                vals = ab["equals-ignore-case"] if isinstance(ab["equals-ignore-case"], list) else [ab["equals-ignore-case"]]
                return isinstance(value, str) and value.lower() not in {x.lower() for x in vals}
        vals = ab if isinstance(ab, list) else [ab]
        return value is not None and value not in vals
    if "numeric" in matcher:
        if not _num(value):
            return False
        ops = matcher["numeric"]
        for i in range(0, len(ops), 2):
            op, n = ops[i], ops[i + 1]
            if not {"=": value == n, "<": value < n, "<=": value <= n, ">": value > n, ">=": value >= n}[op]:
                return False
        return True
    if "cidr" in matcher:
        import ipaddress
        try:
            return ipaddress.ip_address(value) in ipaddress.ip_network(matcher["cidr"], strict=False)
        except (ValueError, TypeError):
            return False
    raise ValueError(f"unsupported pattern operator {matcher}")


def matches(pattern: dict, event: dict) -> bool:
    for key, want in pattern.items():
        if key == "$or":
            if not any(matches(alt, event) for alt in want):
                return False
            continue
        have = event.get(key) if isinstance(event, dict) else None
        if isinstance(want, dict):
            if not isinstance(have, dict) or not matches(want, have):
                return False
            continue
        if not isinstance(want, list):
            raise ValueError("pattern leaf values must be arrays")
        exists = [m for m in want if isinstance(m, dict) and "exists" in m]
        if exists:
            present = key in event if isinstance(event, dict) else False
            if present != exists[0]["exists"]:
                return False
            rest = [m for m in want if m not in exists]
            if not rest:
                continue
            want = rest
        values = have if isinstance(have, list) else [have]
        if key not in (event if isinstance(event, dict) else {}):
            return False
        if not any(_value_matches(m, v) for m in want for v in values):
            return False
    return True


# --- bus -------------------------------------------------------------------------------------------
@dataclass
class Target:
    id: str
    invoke: Callable[[dict, Any], Any]            # (event, role_session) -> result; raise to signal failure
    role_arn: str | None = None
    max_attempts: int = 3
    dead_letters: list = field(default_factory=list)


@dataclass
class Rule:
    name: str
    pattern: dict
    targets: list[Target] = field(default_factory=list)
    enabled: bool = True

    @property
    def arn(self):
        return f"arn:aws:events:{REGION}:{ACCOUNT}:rule/{self.name}"


class EventBridge:
    def __init__(self, clock, assume_service_role, audit, seed: int = 0):
        self.clock, self.assume, self.audit = clock, assume_service_role, audit
        self.rules: dict[str, Rule] = {}
        self.queue: deque = deque()
        self.duplicate_rate = 0.0
        self.rng = random.Random(seed)
        self.delivered: list[dict] = []
        self.auto_drain = True
        self._draining = False
        import threading
        self._lock = threading.RLock()

    def put_rule(self, name: str, pattern: dict) -> Rule:
        self.rules[name] = Rule(name, pattern)
        return self.rules[name]

    def put_targets(self, rule: str, target: Target):
        self.rules[rule].targets.append(target)

    def put_event(self, source: str, detail_type: str, detail: dict, resources=()):
        evt = {"version": "0", "id": str(uuid.uuid4()), "detail-type": detail_type, "source": source,
               "account": ACCOUNT, "time": datetime.fromtimestamp(self.clock(), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
               "region": REGION, "resources": list(resources), "detail": detail}
        with self._lock:
            for rule in self.rules.values():
                if rule.enabled and matches(rule.pattern, evt):
                    for t in rule.targets:
                        self.queue.append((rule, t, evt))
                        if self.duplicate_rate and self.rng.random() < self.duplicate_rate:
                            self.queue.append((rule, t, evt))      # at-least-once: same event id twice
            busy = self._draining
        if self.auto_drain and not busy:
            self.drain()
        return evt

    def drain(self) -> int:
        n = 0
        with self._lock:
            if self._draining:
                return 0
            self._draining = True
        try:
            while True:
                with self._lock:
                    if not self.queue:
                        self._draining = False
                        break
                    rule, t, evt = self.queue.popleft()
                n += 1
                for attempt in range(1, t.max_attempts + 1):
                    try:
                        session = self.assume(t.role_arn, f"events-{rule.name}"[:64]) if t.role_arn else None
                        result = t.invoke(evt, session)
                        self.delivered.append(dict(rule=rule.name, target=t.id, event_id=evt["id"], attempt=attempt,
                                                   result=result))
                        break
                    except Exception as e:                       # noqa: BLE001 - delivery failures go to the DLQ
                        if attempt == t.max_attempts:
                            t.dead_letters.append(dict(event=evt, error=f"{type(e).__name__}: {e}", attempts=attempt))
                            self.audit.record("events.amazonaws.com", "InvokeTarget", None,
                                              {"rule": rule.name, "target": t.id},
                                              error=("FailedInvocation", str(e)[:300]), resources=[rule.arn])
        finally:
            with self._lock:
                self._draining = False
        return n
