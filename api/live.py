"""Live step events from a running ``Analyzer`` (contract v1 ``step.*`` / ``escalation``).

``LiveTap`` wraps any LLM object with ``complete(**kw) -> Completion``. The analyzer loop calls the
model once per turn and passes the previous turn's tool results in ``messages[-1]``, so the tap sees
every tool call when the model makes it (-> ``*_running``) and its outcome on the next call
(-> ``*_result``). That makes steps stream *during* the run without touching ``analyzer/``.

PHI: the tap reads tool payloads only for ``row_count``, column names and ``truncated``. It never
copies row values into an event.
"""
from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field

from api.ids import new_id

# USD per million tokens (input, output). Static estimate table; demo mode never bills anything.
PRICES = {"haiku": (1.0, 5.0), "sonnet": (3.0, 15.0), "opus": (15.0, 75.0)}
_EXPLORE = {"list_relations": "list_views", "describe_relation": "describe_view", "sample_rows": "sample_values",
            "lookup_glossary": "lookup_glossary", "lookup_dictionary": "lookup_dictionary",
            "lookup_examples": "search_examples", "profile": "profile", "lookup_values": "sample_values",
            "query_metrics": "lookup_dictionary", "request_escalation": "request_escalation"}


class Cancelled(Exception):
    """Raised inside the analyzer thread when the user stops the run."""


def tier(model: str) -> str:
    m = model.lower()
    return "small" if "haiku" in m else "large" if "opus" in m else "medium"


def price(model: str, input_tokens: int, output_tokens: int) -> float:
    m = model.lower()
    pin, pout = next((v for k, v in PRICES.items() if k in m), PRICES["sonnet"])
    return round(input_tokens / 1e6 * pin + output_tokens / 1e6 * pout, 6)


def scrub(text: str, limit: int = 300) -> str:
    """Error text without literals: quoted strings, dates, e-mail addresses, IDs mixing letters and digits, and long
    digit runs are masked (adapter spec R2: a database error can echo a value from the data)."""
    text = re.sub(r"'[^']*'", "'…'", str(text or ""))
    text = re.sub(r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}\b", "…", text)
    text = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "…", text)
    text = re.sub(r"\b(?=[A-Za-z0-9]*[A-Za-z])(?=[A-Za-z0-9]*\d)[A-Za-z0-9]{6,}\b", "…", text)
    text = re.sub(r"\d{4,}", "…", text)
    return text[:limit]


def _error_code(message: str) -> str:
    m = message.lower()
    for needle, code in (("binder", "binder_error"), ("not found", "binder_error"), ("does not have a column", "binder_error"),
                         ("parser", "syntax_error"), ("syntax", "syntax_error"), ("timeout", "timeout"),
                         ("time limit", "timeout"), ("guard", "guard_blocked"), ("not allowed", "guard_blocked")):
        if needle in m:
            return code
    return "query_failed"


def _approx_tokens(obj) -> int:
    return max(1, len(json.dumps(obj, default=str)) // 4)



def partition_ms(stages: dict, total_ms: int) -> dict:
    """Whole-ms stages that sum to exactly ``total_ms`` (CONTRACT.md §7), however loaded the machine is.

    The tap's clock starts after the run's (request parsing, history) and is read again after ``totalMs`` was taken,
    so the raw wall-clock stages differ from ``totalMs`` by a few ms, more under load. Time before the tap started is
    planning, so a shortfall goes to ``plan``; an excess is taken from the largest stages. Rounding uses largest
    remainders, so no stage moves by more than the drift plus 1 ms, and none goes negative."""
    raw = {k: max(0.0, float(stages.get(k, 0.0))) for k in STAGES}
    total_ms = max(0, int(total_ms))
    drift = total_ms - sum(raw.values())
    if drift > 0:
        raw["plan"] += drift
    elif drift < 0:
        excess = -drift
        for k in sorted(raw, key=raw.get, reverse=True):
            take = min(raw[k], excess)
            raw[k] -= take
            excess -= take
            if excess <= 0:
                break
    out = {k: int(v) for k, v in raw.items()}
    short = total_ms - sum(out.values())
    for k in sorted(raw, key=lambda k: raw[k] - out[k], reverse=True)[:max(0, short)]:
        out[k] += 1
    return out

@dataclass
class _Pending:
    step_id: str
    name: str
    arguments: dict
    started: float
    candidate: int


@dataclass
class TapStats:
    model_ms: float = 0.0
    sql_ms: float = 0.0
    first_step_ms: float | None = None
    queries: int = 0
    models: dict = field(default_factory=dict)  # model -> {inputTokens, outputTokens, calls}
    #: wall-clock ms per stage (CONTRACT.md §7 ``latency.stages``); each instant belongs to exactly one stage
    stages: dict = field(default_factory=lambda: dict.fromkeys(STAGES, 0.0))


#: per-stage latency buckets, in pipeline order: planning (model turns + schema exploration), running SQL,
#: repairing after a failed query or verification (model + SQL), result checks, and writing the answer.
STAGES = ("plan", "sql", "repair", "checks", "render")


class LiveTap:
    def __init__(self, inner, emit, *, cancelled: threading.Event | None = None, model_delay: float = 0.0,
                 started: float | None = None, multi_candidate: bool = False):
        self.inner = inner
        self.emit = emit
        self.cancelled = cancelled or threading.Event()
        self.model_delay = model_delay
        self.started = started or time.perf_counter()
        self.multi = multi_candidate
        self.stats = TapStats()
        self.pending: dict[str, _Pending] = {}       # tool_use_id -> pending step
        self.failed: dict[int, tuple[str, dict]] = {}  # candidate -> (failed stepId, error) awaiting repair
        self.last_model: dict[int, str] = {}
        self.final_steps: dict[int, str] = {}        # candidate -> stepId of its last final_answer SQL
        self.candidate_steps: dict[int, list[str]] = {}
        self.ask_args: dict | None = None
        self.refuse_args: dict | None = None
        self.repairs: dict[int, int] = {}
        self.reasoning_id = new_id("p_")
        self.reasoning_started: float | None = None
        self._lock = threading.Lock()
        self._escalated_from: str | None = None
        self.calls = getattr(inner, "calls", [])
        self._stage = "plan"
        self._stage_t = self.started

    def enter(self, stage: str) -> None:
        """Close the running stage at now and start ``stage`` (wall clock, so stages sum to the run time)."""
        now = time.perf_counter()
        self.stats.stages[self._stage] += (now - self._stage_t) * 1000
        self._stage, self._stage_t = stage, now

    # -- helpers
    def _ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)

    def _step(self, payload: dict):
        if self.stats.first_step_ms is None:
            self.stats.first_step_ms = self._ms()
        self.emit(payload)

    def _check_cancel(self):
        if self.cancelled.is_set():
            raise Cancelled()

    # -- the LLM interface the analyzer calls
    def complete(self, **kw):
        self._check_cancel()
        model = kw.get("model", "model")
        cand = int(kw.get("candidate_index") or 0)
        with self._lock:
            self._consume_results(kw.get("messages") or [], cand)
            prev = self.last_model.get(cand)
            if prev and prev != model:
                trigger = "repair_exhausted" if cand in self.failed else "verifier_fail"
                self.failed.pop(cand, None)
                self.emit({"type": "escalation", "from": {"model": prev, "tier": tier(prev)},
                           "to": {"model": model, "tier": tier(model)}, "trigger": trigger,
                           "reason": ("The smaller model could not produce a working query, so a larger model "
                                      "is taking over." if trigger == "repair_exhausted" else
                                      "The smaller model's answer did not pass verification, so a larger model "
                                      "is re-checking it.")})
            self.last_model[cand] = model
        t0 = time.perf_counter()
        if self.model_delay:
            end = t0 + self.model_delay
            while time.perf_counter() < end:
                self._check_cancel()
                time.sleep(min(0.05, max(0.0, end - time.perf_counter())))
        completion = self.inner.complete(**kw)
        self.stats.model_ms += (time.perf_counter() - t0) * 1000
        usage = getattr(completion, "usage", {}) or {}
        tin = int(usage.get("input_tokens") or 0) or _approx_tokens([kw.get("system"), kw.get("messages")])
        tout = int(usage.get("output_tokens") or 0) or _approx_tokens(
            [completion.text, [(c.name, c.arguments) for c in completion.tool_calls]])
        row = self.stats.models.setdefault(model, {"inputTokens": 0, "outputTokens": 0, "calls": 0})
        row["inputTokens"] += tin
        row["outputTokens"] += tout
        row["calls"] += 1
        self._check_cancel()
        with self._lock:
            if completion.text and cand == 0:
                if self.reasoning_started is None:
                    self.reasoning_started = time.perf_counter()
                    self.emit({"type": "reasoning.delta", "partId": self.reasoning_id, "delta": completion.text})
                else:
                    self.emit({"type": "reasoning.delta", "partId": self.reasoning_id, "delta": " " + completion.text})
            for call in completion.tool_calls:
                self._start_call(call, cand)
        return completion

    def __getattr__(self, name):  # pass through anything else the analyzer may read
        return getattr(self.inner, name)

    # -- turning calls into events
    def _start_call(self, call, cand: int):
        name, args = call.name, dict(call.arguments or {})
        step_id = new_id("s_")
        now = time.perf_counter()
        if name in ("ask", "refuse"):
            if name == "ask":
                self.ask_args = args
            else:
                self.refuse_args = args
            return
        if name in ("run_sql", "final_answer"):
            sql = str(args.get("sql") or "")
            if not sql:
                return
            self.stats.queries += 1
            failed = self.failed.pop(cand, None)
            ev = {"type": "step.sql_running", "stepId": step_id, "sql": sql, "attempt": 1,
                  "purpose": "candidate" if self.multi else "answer",
                  "label": "Final query" if name == "final_answer" else "Running query"}
            if self.multi:
                ev["candidateId"] = f"k{cand + 1}"
            if failed:
                repair_id = new_id("s_")
                fail_step, error = failed
                n = self.repairs[cand] = self.repairs.get(cand, 0) + 1
                attempt = n + 1
                self._step({"type": "step.repair_attempt", "stepId": repair_id, "failedStepId": fail_step,
                            "attempt": n, "maxAttempts": 3, "error": {"code": error["code"], "message": error["message"]},
                            "revisedSql": sql, "explanation": "Revised the query after the database error."})
                ev.update(purpose="repair", repairOf=repair_id, attempt=attempt)
            self.enter("repair" if failed else "sql")
            self._step(ev)
            self.emit({"type": "status", "label": "Running query"})
            if name == "final_answer":
                self.final_steps[cand] = step_id
            self.candidate_steps.setdefault(cand, []).append(step_id)
            self.pending[call.id] = _Pending(step_id, name, args, now, cand)
            return
        self.emit({"type": "status", "label": f"Exploring {args.get('relation') or 'the schema'}"})
        self.pending[call.id] = _Pending(step_id, name, args, now, cand)

    def _consume_results(self, messages: list, cand: int):
        if not messages or messages[-1].get("role") != "user" or not isinstance(messages[-1].get("content"), list):
            return
        for block in messages[-1]["content"]:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            pend = self.pending.pop(block.get("tool_use_id"), None)
            if pend is None:
                continue
            self._finish_call(pend, str(block.get("content") or ""))

    def _finish_call(self, pend: _Pending, content: str, rows: int | None = None, columns: list | None = None):
        elapsed = int((time.perf_counter() - pend.started) * 1000)
        if pend.name in ("run_sql", "final_answer"):
            self.stats.sql_ms += elapsed
            self.enter("plan")  # the model reads the result next; a failure below switches to repair
            is_error = content.startswith("ERROR:") or content.startswith("final SQL failed:")
            if pend.name == "final_answer" and content.startswith("result failed verification:"):
                self._step({"type": "step.sql_result", "stepId": pend.step_id, "ok": True, "elapsedMs": elapsed})
                self._step({"type": "step.check", "stepId": new_id("s_"), "check": "verifier", "verdict": "fail",
                            "message": scrub(content.split(":", 1)[1].split(". Revise")[0], 400),
                            "targetStepId": pend.step_id, "action": "repair"})
                self.enter("repair")
                return
            if is_error:
                message = scrub(content.split(":", 1)[1].strip().split(". Revise the SQL")[0], 400)
                err = {"code": _error_code(message), "message": message}
                self._step({"type": "step.sql_result", "stepId": pend.step_id, "ok": False, "elapsedMs": elapsed,
                            "error": {"code": err["code"], "message": message}})
                self.failed[pend.candidate] = (pend.step_id, err)
                self.enter("repair")
                return
            ev = {"type": "step.sql_result", "stepId": pend.step_id, "ok": True, "elapsedMs": elapsed}
            if rows is None:
                try:
                    payload = json.loads(content)
                except (ValueError, TypeError):
                    payload = {}
                rows = payload.get("row_count")
                columns = payload.get("columns")
                if payload.get("truncated"):
                    ev["truncated"] = True
            if rows is not None:
                ev["rowCount"] = int(rows)
            if columns:
                ev["columns"] = [{"name": str(c)} for c in columns]
            self._step(ev)
            return
        probe = _EXPLORE.get(pend.name, pend.name)
        target = pend.arguments.get("relation") or pend.arguments.get("term") or pend.arguments.get("query")
        ok = not content.startswith("ERROR:")
        try:
            payload = json.loads(content) if ok else {}
        except (ValueError, TypeError):
            payload = {}
        if not ok:
            summary = "Failed: " + scrub(content[6:], 200)
        elif "columns" in payload and isinstance(payload["columns"], list) and pend.name != "sample_rows":
            summary = f"{len(payload['columns'])} columns"
        elif "relations" in payload and isinstance(payload["relations"], list):
            summary = f"{len(payload['relations'])} views"
        elif "row_count" in payload:
            summary = f"{payload['row_count']} rows sampled"
        elif "examples" in payload:
            summary = f"{len(payload['examples'])} similar questions"
        else:
            summary = "Done"
        ev = {"type": "step.explore", "stepId": pend.step_id, "probe": probe, "summary": summary, "elapsedMs": elapsed}
        if target:
            ev["target"] = str(target)[:120]
        self._step(ev)

    # -- after Analyzer.answer() returns
    def finish(self, answer) -> None:
        """Close steps whose results the analyzer consumed internally (final SQL), then checks + candidates."""
        with self._lock:
            for tool_use_id, pend in list(self.pending.items()):
                if pend.name == "final_answer":
                    is_chosen = answer is not None and (answer.candidate_index or 0) == pend.candidate
                    if answer is not None and answer.kind == "answer" and is_chosen:
                        cols = list(answer.columns or [])
                        self._finish_call(pend, "{}", rows=len(answer.rows or []), columns=cols)
                    else:
                        self._finish_call(pend, "{}")
                    del self.pending[tool_use_id]
            self.pending.clear()
            self.enter("checks")
            if self.reasoning_started is not None:
                self.emit({"type": "reasoning.end", "partId": self.reasoning_id,
                           "durationMs": int((time.perf_counter() - self.reasoning_started) * 1000)})
            if answer is None or answer.kind not in ("answer", "abstain"):
                return
            if answer.kind == "answer":
                target = self.final_steps.get(answer.candidate_index or 0)
                warnings = [w for w in (answer.warnings or []) if "redacted" not in str(w)]
                ev = {"type": "step.check", "stepId": new_id("s_"), "check": "verifier",
                      "verdict": "warn" if warnings else "pass",
                      "message": scrub("; ".join(map(str, warnings)), 400) if warnings
                      else f"Result checks passed ({len(answer.rows or [])} rows).",
                      "action": "caveat" if warnings else "none"}
                if target:
                    ev["targetStepId"] = target
                self._step(ev)
            results = getattr(answer, "candidate_results", None) or []
            if len(results) > 1:
                cands = []
                for r in results:
                    idx = r.get("candidate_index")
                    cid = f"k{(idx or 0) + 1}"
                    c = {"candidateId": cid, "sql": r.get("sql") or "", "ok": r.get("kind") == "answer"}
                    if r.get("row_count") is not None:
                        c["rowCount"] = int(r["row_count"])
                    step = self.final_steps.get(idx or 0)
                    if step:
                        c["stepId"] = step
                    cands.append(c)
                norm = lambda s: re.sub(r"\s+", " ", (s or "").strip().lower())
                for c in cands:
                    c["agreesWith"] = [o["candidateId"] for o in cands if o is not c and o["ok"] and c["ok"]
                                       and o.get("rowCount") == c.get("rowCount") and norm(o["sql"]) == norm(c["sql"])]
                chosen = None if answer.kind != "answer" else f"k{(answer.candidate_index or 0) + 1}"
                agree = len(cands[0]["agreesWith"]) + 1 if cands else 0
                sel = getattr(answer, "selection", None) or {}
                self._step({"type": "step.candidates", "stepId": new_id("s_"), "candidates": cands,
                            "chosenId": chosen, "method": "execution_agreement" if "vote" in str(sel.get("mode", "vote")) or
                            sel.get("mode") == "agree_or_abstain" else str(sel.get("mode")),
                            "rationale": (f"{max(agree, 1)} of {len(cands)} candidates returned the same result."
                                          if chosen else "The candidates disagreed, so no answer was chosen.")})

    def abort(self) -> None:
        """The run was cancelled: close every step still in flight so no UI row is left spinning.

        Open SQL steps get ``step.sql_result {ok: false, error: {code: "cancelled"}}``; an open reasoning part gets
        ``reasoning.end``. Explore steps only emit on completion, so there is nothing to close (CONTRACT.md §8)."""
        with self._lock:
            for pend in list(self.pending.values()):
                if pend.name in ("run_sql", "final_answer"):
                    self._step({"type": "step.sql_result", "stepId": pend.step_id, "ok": False,
                                "elapsedMs": int((time.perf_counter() - pend.started) * 1000),
                                "error": {"code": "cancelled", "message": "Stopped by the user."}})
            self.pending.clear()
            if self.reasoning_started is not None:
                self.emit({"type": "reasoning.end", "partId": self.reasoning_id,
                           "durationMs": int((time.perf_counter() - self.reasoning_started) * 1000)})
                self.reasoning_started = None

    def usage(self, elapsed_ms: int, first_event_ms: int | None, first_token_ms: int | None) -> dict:
        models = []
        tin = tout = 0
        cost = 0.0
        for model, row in self.stats.models.items():
            c = price(model, row["inputTokens"], row["outputTokens"])
            models.append({"model": model, "role": "analyst", "inputTokens": row["inputTokens"],
                           "outputTokens": row["outputTokens"], "costUsd": c, "calls": row["calls"]})
            tin += row["inputTokens"]
            tout += row["outputTokens"]
            cost += c
        self.enter(self._stage)
        latency = {"totalMs": elapsed_ms, "modelMs": int(self.stats.model_ms), "sqlMs": int(self.stats.sql_ms),
                   "stages": partition_ms(self.stats.stages, elapsed_ms)}
        if first_event_ms is not None:
            latency["firstEventMs"] = first_event_ms
        if self.stats.first_step_ms is not None:
            latency["firstStepMs"] = self.stats.first_step_ms
        if first_token_ms is not None:
            latency["firstTokenMs"] = first_token_ms
        out = {"queries": self.stats.queries, "elapsedMs": elapsed_ms, "latency": latency,
               "costIsEstimate": True, "costUsd": round(cost, 6)}
        if models:
            out["tokens"] = {"input": tin, "output": tout}
            out["models"] = models
        return out
