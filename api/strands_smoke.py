"""``python -m api.strands_smoke "<question>"``: one question through the real analyzer path on your machine.

Opens a session on the bench snapshot (``SnapshotOpener``: the acme_labs data the benchmark uses, read-only and
sandboxed, escalation audited by ``ReasonedEscalationOpener``), runs B1 on a Strands agent with Claude on Amazon
Bedrock (profile ``CC_AWS_PROFILE``, default ``apiservice``; region ``CC_AWS_REGION`` or the profile's; model
``CC_STRANDS_MODEL_ID``, default ``us.anthropic.claude-haiku-5-5``), and prints each step as it happens, then the
answer, the first rows and the token usage (with prompt-cache reads and writes). Bedrock is paid: it needs
``CC_API_ALLOW_PAID_API=i-understand-this-calls-a-paid-model-api``.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
from pathlib import Path

from analyzer.opener import SnapshotOpener

from api.backends import ReasonedEscalationOpener
from api.live import LiveTap
from api.strands_analyzer import AnalyzerSettings, build_analyzer

SHOWN_ROWS = 10


def describe_event(event: dict) -> str | None:
    """One readable line per step event; None for events the smoke run does not print."""
    kind = event.get("type")
    if kind == "step.sql_running":
        return f"[{event['label']}] {' '.join(event['sql'].split())[:300]}"
    if kind == "step.sql_result":
        return (f"  -> {event.get('rowCount')} rows in {event['elapsedMs']} ms" if event["ok"]
                else f"  -> failed: {event['error']['message']}")
    if kind == "step.repair_attempt":
        return f"[repair {event['attempt']}] after: {event['error']['message']}"
    if kind == "step.explore":
        return f"[{event['probe']}] {event.get('target') or ''} {event['summary']}".replace("  ", " ")
    if kind == "step.check":
        return f"[check] {event['verdict']}: {event['message']}"
    return None


def main(argv: list[str] | None = None, out=sys.stdout, make_analyzer=build_analyzer) -> int:
    """``make_analyzer`` is for tests (a scripted model); the command always builds the Bedrock one."""
    parser = argparse.ArgumentParser(prog="python -m api.strands_smoke", description=__doc__.split("\n\n")[0])
    parser.add_argument("question")
    parser.add_argument("--snapshot", help="bench snapshot directory (default: CC_API_ANALYZER_SNAPSHOT, else the "
                                           "cached bench snapshot, built on first use)")
    args = parser.parse_args(argv)
    settings = AnalyzerSettings()
    settings.model_kind = "bedrock"
    if args.snapshot:
        settings.snapshot_path = args.snapshot
    if not settings.snapshot_path:
        from bench.dataset import snapshot
        settings.snapshot_path = str(snapshot())
    analyzer = make_analyzer(settings, signed_in_customer_data=True)

    def print_event(event: dict) -> None:
        line = describe_event(event)
        if line:
            print(line, file=out, flush=True)

    def print_escalation(reason: str) -> None:
        print(f"[escalation granted] {reason}", file=out, flush=True)

    cancelled = threading.Event()
    tap = LiveTap(print_event, cancelled=cancelled)
    opener = ReasonedEscalationOpener(SnapshotOpener(Path(settings.snapshot_path)),
                                      on_escalation_granted=print_escalation)
    print(f"model: {analyzer.model_label}  snapshot: {settings.snapshot_path}", file=out, flush=True)
    answer = analyzer.answer(args.question, tap, cancelled, opener)
    tap.finish(answer)
    print(f"\n{answer.kind}: {answer.text}", file=out)
    if answer.kind == "answer":
        print(f"SQL: {answer.sql}", file=out)
        print(json.dumps(answer.columns), file=out)
        for row in (answer.rows or [])[:SHOWN_ROWS]:
            print(json.dumps(row, default=str), file=out)
        print(f"({len(answer.rows or [])} rows)", file=out)
    if answer.questions:
        print(json.dumps(answer.questions, indent=1), file=out)
    print(f"usage: {json.dumps(answer.usage)}", file=out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
