"""Shell over the analyzer tools for the subagent harness.

    python -m analyzer.cli --session <dir> <command>

Each command opens a snapshot session through ``SnapshotOpener`` (the bench's
sandboxed DuckDB, not a second query path). Model SQL still goes through the
guard. ``escalate`` is the only command that mounts ``lossless.*``, and it
appends an audit record before the next command is allowed to see those views.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bench.agent_cli import INVESTIGATIVE_COMMANDS
from bench.guard import GuardError

from analyzer.harness import config_from_meta
from analyzer.opener import SnapshotOpener
from analyzer.tools import ToolError, ToolLayer


def _read_calls(session_dir: Path) -> list[dict]:
    path = session_dir / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _log(session_dir: Path, command: str, argument, ok: bool, result_summary):
    with open(session_dir / "calls.jsonl", "a") as handle:
        handle.write(json.dumps({"command": command, "argument": argument, "ok": ok, "result": result_summary},
                                default=str) + "\n")


def _audit_path(session_dir: Path) -> Path:
    return session_dir / "audit.jsonl"


def _escalation_path(session_dir: Path) -> Path:
    return session_dir / "escalation.json"


def _load_escalated(session_dir: Path) -> bool:
    path = _escalation_path(session_dir)
    if not path.exists():
        return False
    return json.loads(path.read_text()).get("escalated") is True


def _tools(session_dir: Path) -> tuple[ToolLayer, dict]:
    meta = json.loads((session_dir / "session.json").read_text())
    config = config_from_meta(meta, for_subagent=True)

    def sink(record: dict):
        with open(_audit_path(session_dir), "a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    tools = ToolLayer(SnapshotOpener(meta["snapshot_path"]), config, question_id=meta.get("question_id"),
                      question_family=meta.get("question_family"), audit_sink=sink,
                      restored_escalation=_load_escalated(session_dir))
    prior = [call for call in _read_calls(session_dir) if call.get("command") in INVESTIGATIVE_COMMANDS and call.get("ok")]
    tools._tool_calls_used = len(prior)
    return tools, meta


def _run(session_dir: Path, command: str, argument: dict):
    tools, _meta = _tools(session_dir)
    if command == "list":
        return {"relations": tools.list_relations()}
    if command == "describe":
        return tools.describe_relation(argument["relation"])
    if command == "sample":
        return tools.sample_rows(argument["relation"], argument.get("n"))
    if command == "profile":
        return tools.profile(argument.get("relation"))
    if command == "dictionary":
        return tools.lookup_dictionary(argument.get("relation"))
    if command == "glossary":
        return tools.lookup_glossary(argument.get("term"))
    if command == "sql":
        return tools.run_sql(argument["sql"])
    if command == "escalate":
        result = tools.request_escalation(argument["reason"])
        _escalation_path(session_dir).write_text(json.dumps({
            "escalated": True, "reason": argument["reason"], "customer": result.get("customer")}))
        return result
    if command == "metrics":
        return tools.query_metrics(json.loads(argument["request_json"]))
    if command == "lookup":
        return tools.lookup_values(argument["dimension"], argument["text"])
    if command == "examples":
        return tools.lookup_examples(argument["question"])
    if command == "answer":
        digest = tools.run_sql(argument["sql"], charge=False)
        final = session_dir / "final.json"
        if final.exists():
            raise ToolError("an answer was already submitted")
        final.write_text(json.dumps({"kind": "answer", "sql": argument["sql"], "text": argument.get("text") or "",
                                      "via": "sql"}))
        return {"recorded": True, "row_count": digest["row_count"], "warnings": digest.get("warnings") or []}
    if command == "refuse":
        final = session_dir / "final.json"
        if final.exists():
            raise ToolError("an answer was already submitted")
        final.write_text(json.dumps({"kind": "refusal", "text": argument["reason"]}))
        tools._audit("refusal", reason=argument["reason"])
        return {"recorded": True}
    if command == "ask":
        if not tools.config.enable_ask:
            raise ToolError("ask is not enabled in this session")
        final = session_dir / "final.json"
        if final.exists():
            raise ToolError("an answer was already submitted")
        final.write_text(json.dumps({"kind": "clarification", "text": argument["question"]}))
        return {"recorded": True}
    raise ToolError(f"unknown command {command}")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="CC analyzer tools for one bench session")
    parser.add_argument("--session", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("briefing")
    sub.add_parser("list")
    describe = sub.add_parser("describe")
    describe.add_argument("relation")
    sample = sub.add_parser("sample")
    sample.add_argument("relation")
    sample.add_argument("--n", type=int)
    profile = sub.add_parser("profile")
    profile.add_argument("relation", nargs="?")
    dictionary = sub.add_parser("dictionary")
    dictionary.add_argument("relation", nargs="?")
    glossary = sub.add_parser("glossary")
    glossary.add_argument("term", nargs="?")
    sql = sub.add_parser("sql")
    sql.add_argument("query")
    escalate = sub.add_parser("escalate")
    escalate.add_argument("reason")
    metrics = sub.add_parser("metrics")
    metrics.add_argument("request_json")
    lookup = sub.add_parser("lookup")
    lookup.add_argument("dimension")
    lookup.add_argument("text")
    examples = sub.add_parser("examples")
    examples.add_argument("question")
    answer = sub.add_parser("answer")
    answer.add_argument("--sql", required=True)
    answer.add_argument("--text", default="")
    refuse = sub.add_parser("refuse")
    refuse.add_argument("reason")
    ask = sub.add_parser("ask")
    ask.add_argument("question")
    args = parser.parse_args(argv)
    session_dir = Path(args.session)
    if args.command == "briefing":
        from analyzer.harness import build_subagent_prompt
        print(build_subagent_prompt(session_dir))
        return 0
    argument = {key: value for key, value in vars(args).items() if key not in {"session", "command"} and value is not None}
    if args.command == "sql":
        argument = {"sql": args.query}
    try:
        result = _run(session_dir, args.command, argument)
    except (ToolError, GuardError, json.JSONDecodeError) as exc:
        _log(session_dir, args.command, argument, False, str(exc)[:300])
        print(f"ERROR: {exc}")
        return 1
    summary = {"row_count": result.get("row_count")} if isinstance(result, dict) and "row_count" in result else "ok"
    _log(session_dir, args.command, argument, True, summary)
    print(result if isinstance(result, str) else json.dumps(result, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
