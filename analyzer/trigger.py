"""#26 escalation trigger: a deterministic verdict over one finished session directory.

    python3 -m analyzer.trigger <session_dir> [--reasons lint_after_repair,repairs_exhausted,low_confidence]

Reads only session.json, calls.jsonl and final.json (no model, no database), so a runner and a later audit get the
same answer. Reasons:

- ``no_answer``: nothing recorded, or a kind other than answer/refusal. A refusal never escalates.
- ``lint_after_repair``: the final SQL still has a domain lint finding (#22, re-linted from the SQL and the
  question) or an unresolved reconciliation finding (#23) after the repair loop.
- ``repairs_exhausted``: the session used its whole repair budget (max_repairs > 0).
- ``low_confidence``: the answer's self-rated --confidence is low.
- ``blocking_warning``: the final result had a blocking result warning (empty, or a rate outside 0..100).

Candidate or route disagreement is the fourth B10 signal; it spans several sessions and lives in
analyzer.orchestration.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analyzer.checks import blocking_warnings
from analyzer.cli import repairs_used
from analyzer.harness import config_from_meta
from analyzer.lint import domain_lint

TRIGGER_REASONS = ("no_answer", "lint_after_repair", "repairs_exhausted", "low_confidence", "blocking_warning")
# The analyzer's pre-existing cascade rule (analyzer.loop._should_cascade), the "off" level of #26 when #17 is on.
CURRENT_ANALYZER_TRIGGER = ("no_answer", "blocking_warning")
# B8: lint still firing after repair, repair budget spent, low self-rated confidence (and nothing to score).
LINT_REPAIR_CONFIDENCE_TRIGGER = ("no_answer", "lint_after_repair", "repairs_exhausted", "low_confidence")
# B2's "failure signal" for the low-then-high effort retry (#12).
FAILURE_SIGNAL_TRIGGER = ("no_answer", "repairs_exhausted", "blocking_warning")


def _final(session_dir: Path) -> dict | None:
    path = session_dir / "final.json"
    return json.loads(path.read_text()) if path.exists() else None


def _calls(session_dir: Path) -> list[dict]:
    path = session_dir / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def evaluate_trigger(session_dir: Path | str, reasons: tuple[str, ...] | list[str]) -> dict:
    """{"escalate": bool, "fired": [reason, ...], "details": {...}} for the reasons asked about."""
    unknown = set(reasons) - set(TRIGGER_REASONS)
    if unknown:
        raise ValueError(f"unknown trigger reasons {sorted(unknown)}")
    session_dir = Path(session_dir)
    meta = json.loads((session_dir / "session.json").read_text())
    config = config_from_meta(meta, for_subagent=True)
    final = _final(session_dir)
    details: dict = {}
    if final is not None and final.get("kind") == "refusal":
        return {"escalate": False, "fired": [], "details": {"refusal": True}}
    answered = final is not None and final.get("kind") == "answer"
    signals = {"no_answer": not answered}
    if answered:
        lint = [finding.render() for finding in domain_lint(final["sql"], meta["question"])]
        unresolved = [item for item in final.get("unresolved_findings") or []
                      if item.split(":", 1)[0] in {"payment_balancing", "rate_parts", "group_shares"}]
        details["lint_findings"] = lint + unresolved
        signals["lint_after_repair"] = bool(lint or unresolved)
        signals["low_confidence"] = final.get("confidence") == "low"
        details["blocking_warnings"] = blocking_warnings(final.get("result_warnings") or [])
        signals["blocking_warning"] = bool(details["blocking_warnings"])
    else:
        signals.update(lint_after_repair=False, low_confidence=False, blocking_warning=False)
    used = repairs_used(_calls(session_dir))
    details["repairs_used"] = used
    details["max_repairs"] = config.max_repairs
    signals["repairs_exhausted"] = config.max_repairs > 0 and used >= config.max_repairs
    fired = [reason for reason in reasons if signals[reason]]
    return {"escalate": bool(fired), "fired": fired, "details": details}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("session_dir")
    parser.add_argument("--reasons", default=",".join(LINT_REPAIR_CONFIDENCE_TRIGGER))
    args = parser.parse_args(argv)
    print(json.dumps(evaluate_trigger(args.session_dir, [r for r in args.reasons.split(",") if r])))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
