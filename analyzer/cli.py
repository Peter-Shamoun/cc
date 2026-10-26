"""Shell over the analyzer tools for the subagent harness.

    python -m analyzer.cli --session <dir> <command>

Each command opens a snapshot session through ``SnapshotOpener`` (the bench's
sandboxed DuckDB, not a second query path). A session created by the chat API for a signed-in user instead carries
``data_channel`` in session.json: the command is then sent over that run's channel (``analyzer.run_channel``) and runs
inside the API process against the run's reader session (``ccsim.query.open_session``); this process never opens a
connection, sees a credential, or names a customer. Model SQL still goes through the
guard. ``escalate`` is the only command that mounts ``lossless.*``, and it
appends an audit record before the next command is allowed to see those views.

Subagent-route parameter levels live here, driven by the session's technique config:

- gates: an interpret record before sql/spec/answer (#10, #11, #25; and again after a
  rejected answer under plan_and_replan), a spec that returned cannot_express before free
  SQL (#8 compiler), a lookup of every gated filter literal (#5 mandatory).
- ``probe`` (#9) runs SQL without charging the command budget, up to A06's probe cap.
- ``sql`` appends domain lint (#22) and reconciliation (#23) findings.
- ``answer`` repairs (#13): blocking result warnings (with result signals), lint and
  reconciliation findings reject the answer with the fix, while repairs remain; a failed
  answer of any kind uses one repair. ``--confidence`` (#26) is stored on final.json.
- role commands: ``interpret`` (planner), ``verdict`` (verifier), ``select`` (selector).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from bench.agent_cli import INVESTIGATIVE_COMMANDS
from bench.guard import GuardError

from analyzer.checks import blocking_warnings
from analyzer.harness import config_from_meta
from analyzer.lint import domain_lint, filter_literals, reconciliation_findings
from analyzer.opener import SnapshotOpener
from analyzer.protocols import (
    CONFIDENCE_LEVELS,
    MAXIMUM_PROBES,
    InterpretationError,
    required_interpretation_parts,
    validate_interpretation,
)
from analyzer.tools import ToolError, ToolLayer, clarification_request

UNCHARGED_COMMANDS = ("probe",)


class AnswerNotRecorded(ToolError):
    """The answer's SQL failed, or a check asked for a repair. Either way it used one repair (#13)."""


class RepairRequested(AnswerNotRecorded):
    """The answer was not recorded: a check found a fixable problem and repairs remain."""


def _read_calls(session_dir: Path) -> list[dict]:
    path = session_dir / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _log(session_dir: Path, command: str, argument, ok: bool, result_summary, used_repair: bool = False):
    record = {"command": command, "argument": argument, "ok": ok, "result": result_summary}
    if used_repair:
        record["used_repair"] = True
    with open(session_dir / "calls.jsonl", "a") as handle:
        handle.write(json.dumps(record, default=str) + "\n")


def _audit_path(session_dir: Path) -> Path:
    return session_dir / "audit.jsonl"


def _escalation_path(session_dir: Path) -> Path:
    return session_dir / "escalation.json"


def _interpretation_path(session_dir: Path) -> Path:
    return session_dir / "interpretation.jsonl"


def _query_seconds_spent(session_dir: Path) -> float:
    """Model SQL time earlier commands of this session spent: every guarded query's audit record carries seconds."""
    path = _audit_path(session_dir)
    if not path.exists():
        return 0.0
    return sum(json.loads(line).get("seconds", 0.0) for line in path.read_text().splitlines() if line.strip())


def _load_escalated(session_dir: Path) -> bool:
    path = _escalation_path(session_dir)
    if not path.exists():
        return False
    return json.loads(path.read_text()).get("escalated") is True


def latest_interpretation(session_dir: Path) -> dict | None:
    path = _interpretation_path(Path(session_dir))
    if not path.exists():
        return None
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    return json.loads(lines[-1]) if lines else None


def repairs_used(calls: list[dict]) -> int:
    """Answers whose SQL failed or that a check sent back. Usage errors (a missing flag, a gate) do not count."""
    return sum(1 for call in calls if call.get("command") == "answer" and call.get("used_repair"))


def _session_opener(meta: dict, opener):
    """The opener a command queries through. A session whose data is reached over its run channel never falls back
    to a local snapshot: only the API process that holds the run's reader session may answer for it."""
    if opener is not None:
        return opener
    if meta.get("data_channel"):
        raise ToolError("this session's data is only reachable through its run channel")
    return SnapshotOpener(meta["snapshot_path"])


def _tools(session_dir: Path, opener=None) -> tuple[ToolLayer, dict]:
    meta = json.loads((session_dir / "session.json").read_text())
    config = config_from_meta(meta, for_subagent=True)

    def sink(record: dict):
        with open(_audit_path(session_dir), "a") as handle:
            handle.write(json.dumps(record, default=str) + "\n")

    tools = ToolLayer(_session_opener(meta, opener), config, question_id=meta.get("question_id"),
                      question_family=meta.get("question_family"), audit_sink=sink,
                      restored_escalation=_load_escalated(session_dir),
                      restored_query_seconds=_query_seconds_spent(session_dir))
    prior = [call for call in _read_calls(session_dir) if call.get("command") in INVESTIGATIVE_COMMANDS
             and call.get("command") not in UNCHARGED_COMMANDS and call.get("ok")]
    tools._tool_calls_used = len(prior)
    return tools, meta


def _require_interpretation(session_dir: Path, tools: ToolLayer, command: str):
    config = tools.config
    if not config.interpretation_required() or config.plan_mode == "external_plan":
        return
    calls = _read_calls(session_dir)
    interpret_indexes = [i for i, call in enumerate(calls) if call.get("command") == "interpret" and call.get("ok")]
    if not interpret_indexes:
        raise ToolError(f"{command} is rejected until an interpret record is accepted: run interpret first")
    if config.plan_mode == "plan_and_replan":
        failed_answers = [i for i, call in enumerate(calls) if call.get("command") == "answer" and call.get("used_repair")]
        if failed_answers and failed_answers[-1] > interpret_indexes[-1]:
            raise ToolError(f"{command} is rejected until you replan: record a revised interpret after the "
                            "rejected answer")


def _compiler_fallback_open(session_dir: Path) -> bool:
    from analyst.approaches.a03_metric_spec.adapter import sql_fallback_allowed
    return sql_fallback_allowed(_read_calls(session_dir))


def _require_sql_allowed(session_dir: Path, tools: ToolLayer, command: str):
    if tools.config.generation_mode.startswith("compiler") and not _compiler_fallback_open(session_dir):
        raise ToolError(f"{command} with free SQL is available only after a spec returns cannot_express")


def _require_lookups(session_dir: Path, tools: ToolLayer, sql: str):
    """#5 mandatory: each gated filter literal must be a value a lookup in this session returned."""
    if not tools.config.lookup_mandatory_before_literals:
        return
    returned: dict[str, set] = {}
    for call in _read_calls(session_dir):
        if call.get("command") == "lookup" and call.get("ok") and isinstance(call.get("result"), dict):
            returned.setdefault(call["argument"]["dimension"], set()).update(call["result"].get("matches") or [])
    missing = []
    for dimension, literal, is_pattern in filter_literals(sql):
        if dimension not in returned or (not is_pattern and literal not in returned[dimension]):
            missing.append(f"{dimension} = {literal!r}")
    if missing:
        raise ToolError("look up each filter value before using it, then copy the stored spelling: "
                        + "; ".join(f"lookup {item.split(' = ')[0]} \"<text>\" for {item}" for item in missing))


def _findings(tools: ToolLayer, meta: dict, sql: str, digest: dict) -> list[str]:
    config = tools.config
    found = []
    if config.domain_lint:
        found += [finding.render() for finding in domain_lint(sql, meta["question"])]
    if config.reconciliation_checks:
        found += [finding.render() for finding in reconciliation_findings(
            sql, meta["question"], digest["columns"], digest.get("all_rows", digest["rows"]), tools.session.con)]
    return found


def _run_query(session_dir: Path, tools: ToolLayer, meta: dict, sql: str, *, charge: bool) -> dict:
    digest = tools.run_sql(sql, charge=charge, include_all_rows=tools.config.reconciliation_checks)
    findings = _findings(tools, meta, sql, digest)
    digest.pop("all_rows", None)
    if findings:
        digest["lint"] = findings
    return digest


def _compiled_spec_sql(request_json: str) -> str:
    from analyst.approaches.a03_metric_spec.compiler import CannotExpress, SpecError, compile_spec
    try:
        spec = json.loads(request_json)
    except json.JSONDecodeError as exc:
        raise ToolError(f"spec: {exc}") from None
    try:
        return compile_spec(spec).sql
    except SpecError as exc:
        raise ToolError(f"spec: {exc}") from None
    except CannotExpress as exc:
        raise ToolError(f"cannot_express: {exc.reason}" + (f" Hint: {exc.hint}" if exc.hint else "")) from None


def _record_final(session_dir: Path, payload: dict):
    final = session_dir / "final.json"
    if final.exists():
        raise ToolError("an answer was already submitted")
    final.write_text(json.dumps(payload))


def _answer(session_dir: Path, tools: ToolLayer, meta: dict, argument: dict) -> dict:
    config = tools.config
    if (session_dir / "final.json").exists():
        raise ToolError("an answer was already submitted")
    if bool(argument.get("sql")) == bool(argument.get("spec")):
        raise ToolError("give exactly one of --sql or --spec")
    confidence = argument.get("confidence")
    if confidence is not None and confidence not in CONFIDENCE_LEVELS:
        raise ToolError(f"--confidence must be one of {', '.join(CONFIDENCE_LEVELS)}")
    if config.report_confidence and confidence is None:
        raise ToolError("this session requires --confidence high|medium|low on the answer")
    _require_interpretation(session_dir, tools, "answer")
    if argument.get("spec"):
        if not config.generation_mode.startswith("compiler"):
            raise ToolError("answer --spec is not enabled in this session")
        sql, via = _compiled_spec_sql(argument["spec"]), "compiler"
    else:
        _require_sql_allowed(session_dir, tools, "answer --sql")
        sql, via = argument["sql"], "sql"
    _require_lookups(session_dir, tools, sql)
    try:
        digest = tools.run_sql(sql, charge=False, include_all_rows=True)
    except ToolError as exc:
        raise AnswerNotRecorded(f"final SQL failed: {exc}") from None
    warnings = list(digest.get("warnings") or [])
    blocked = blocking_warnings(warnings) if config.verify and config.self_correct == "errors+result_signals" else []
    findings = blocked + _findings(tools, meta, sql, digest)
    used = repairs_used(_read_calls(session_dir))
    if findings and config.self_correct != "none" and used < config.max_repairs:
        replan = " Record a revised interpret first." if config.plan_mode == "plan_and_replan" else ""
        raise RepairRequested(f"repair {used + 1} of {config.max_repairs}: the answer was not recorded. "
                              + " | ".join(findings) + f" Revise the SQL and run answer again.{replan}")
    _record_final(session_dir, {"kind": "answer", "sql": sql, "text": argument.get("text") or "", "via": via,
                                "confidence": confidence, "result_warnings": warnings,
                                "unresolved_findings": findings, "repairs_used": used})
    return {"recorded": True, "row_count": digest["row_count"], "warnings": warnings, "unresolved_findings": findings}


def _probe(session_dir: Path, tools: ToolLayer, meta: dict, sql: str) -> dict:
    if not tools.config.explore_probes:
        raise ToolError("probe is not enabled in this session")
    used = sum(1 for call in _read_calls(session_dir) if call.get("command") == "probe" and call.get("ok"))
    if used >= MAXIMUM_PROBES:
        raise ToolError(f"the {MAXIMUM_PROBES} probes are used up: write the query now")
    return tools.run_sql(sql, charge=False)


def _spec(session_dir: Path, tools: ToolLayer, meta: dict, request_json: str) -> dict:
    if not tools.config.generation_mode.startswith("compiler"):
        raise ToolError("spec is not enabled in this session")
    _require_interpretation(session_dir, tools, "spec")
    sql = _compiled_spec_sql(request_json)
    return {"compiled_sql": sql, **_run_query(session_dir, tools, meta, sql, charge=True)}


def _interpret(session_dir: Path, tools: ToolLayer, meta: dict, record_json: str) -> dict:
    planner = (meta.get("role") or {}).get("kind") == "planner"
    parts = required_interpretation_parts(tools.config, planner=planner)
    if not parts:
        raise ToolError("interpret is not enabled in this session")
    try:
        record = validate_interpretation(json.loads(record_json), parts)
    except (json.JSONDecodeError, InterpretationError) as exc:
        raise ToolError(f"interpret: {exc}") from None
    with open(_interpretation_path(session_dir), "a") as handle:
        handle.write(json.dumps(record) + "\n")
    return {"recorded": True, "parts": sorted(record)}


def _verdict(session_dir: Path, meta: dict, verdict_json: str) -> dict:
    from analyst.approaches.a05_verifier.prompts import parse_critic_verdict
    if (meta.get("role") or {}).get("kind") != "verifier":
        raise ToolError("verdict is only for a verifier session")
    path = session_dir / "verdict.json"
    if path.exists():
        raise ToolError("a verdict was already recorded")
    parsed = parse_critic_verdict(verdict_json)
    path.write_text(json.dumps(parsed))
    return {"recorded": True, "accept": parsed["accept"]}


def _select(session_dir: Path, meta: dict, choice: str, reason: str) -> dict:
    role = meta.get("role") or {}
    if role.get("kind") != "selector":
        raise ToolError("select is only for a selector session")
    labels = [candidate["label"] for candidate in role["candidates"]]
    if choice not in labels + ["abstain"]:
        raise ToolError(f"choice must be one of {', '.join(labels + ['abstain'])}")
    path = session_dir / "selection.json"
    if path.exists():
        raise ToolError("a choice was already recorded")
    path.write_text(json.dumps({"choice": choice, "reason": reason}))
    return {"recorded": True}


def _run(session_dir: Path, command: str, argument: dict, opener=None):
    meta = json.loads((session_dir / "session.json").read_text())
    if command == "verdict":
        return _verdict(session_dir, meta, argument["verdict_json"])
    if command == "select":
        return _select(session_dir, meta, argument["choice"], argument.get("reason") or "")
    tools, meta = _tools(session_dir, opener)
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
        _require_interpretation(session_dir, tools, "sql")
        _require_sql_allowed(session_dir, tools, "sql")
        _require_lookups(session_dir, tools, argument["sql"])
        return _run_query(session_dir, tools, meta, argument["sql"], charge=True)
    if command == "probe":
        return _probe(session_dir, tools, meta, argument["sql"])
    if command == "spec":
        return _spec(session_dir, tools, meta, argument["request_json"])
    if command == "interpret":
        return _interpret(session_dir, tools, meta, argument["record_json"])
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
        if tools.config.enable_examples:
            raise ToolError("the solved examples for this question are already in your briefing")
        return tools.lookup_examples(argument["question"])
    if command == "answer":
        return _answer(session_dir, tools, meta, argument)
    if command == "refuse":
        _record_final(session_dir, {"kind": "refusal", "text": argument["reason"]})
        tools._audit("refusal", reason=argument["reason"])
        return {"recorded": True}
    if command == "ask":
        if not tools.config.enable_ask:
            raise ToolError("ask is not enabled in this session")
        if (session_dir / "final.json").exists():
            raise ToolError("an answer was already submitted")
        _record_final(session_dir, {"kind": "clarification", "text": argument["question"],
                                    **({"questions": argument["questions"]} if argument["questions"] else {})})
        return {"recorded": True, "questions": len(argument["questions"])}
    raise ToolError(f"unknown command {command}")


def _clarification_argument(argument: dict) -> dict:
    questions = argument.get("questions")
    if isinstance(questions, str):
        try:
            questions = json.loads(questions)
        except json.JSONDecodeError as exc:
            raise ToolError(f"--questions is not valid JSON: {exc.msg}") from exc
    request = clarification_request({"question": argument.get("question"), "questions": questions})
    return {"question": request["text"], "questions": request["questions"]}


def _summary(command: str, result) -> object:
    """What calls.jsonl keeps. Lookups keep the returned spellings for the mandatory-lookup gate."""
    if command == "lookup" and isinstance(result, dict):
        return {"matches": [match.get("value") for match in result.get("matches") or []]}
    if isinstance(result, dict) and "row_count" in result:
        summary = {"row_count": result.get("row_count")}
        if result.get("lint"):
            summary["lint"] = result["lint"]
        return summary
    return "ok"


def build_parser() -> argparse.ArgumentParser:
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
    probe = sub.add_parser("probe")
    probe.add_argument("query")
    spec = sub.add_parser("spec")
    spec.add_argument("request_json")
    interpret = sub.add_parser("interpret")
    interpret.add_argument("record_json")
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
    answer.add_argument("--sql")
    answer.add_argument("--spec")
    answer.add_argument("--text", default="")
    answer.add_argument("--confidence")
    refuse = sub.add_parser("refuse")
    refuse.add_argument("reason")
    ask = sub.add_parser("ask", description="One plain question, or --questions with up to 4 form questions "
                                            "(the positional question is then an optional lead-in).")
    ask.add_argument("question", nargs="?")
    ask.add_argument("--questions", help='JSON list: [{"header": "<=12 chars", "question": "...", '
                                         '"options": [{"label": "...", "description": "..."}], "multiSelect": false}]')
    verdict = sub.add_parser("verdict")
    verdict.add_argument("verdict_json")
    select = sub.add_parser("select")
    select.add_argument("choice")
    select.add_argument("--reason", default="")
    return parser


def execute(argv: list[str], opener=None) -> tuple[int, str]:
    """One command: (exit code, what it prints). ``opener`` is the run's reader session when the API runs the command
    for a session's run channel; otherwise the session's snapshot is opened."""
    args = build_parser().parse_args(argv)
    session_dir = Path(args.session)
    if args.command == "briefing":
        from analyzer.harness import OrchestratedSession, build_subagent_prompt
        from analyzer.playbook import PlaybookNotReady
        try:
            return 0, build_subagent_prompt(session_dir)
        except (OrchestratedSession, PlaybookNotReady) as exc:
            return 1, f"ERROR: {exc}"
    argument = {key: value for key, value in vars(args).items() if key not in {"session", "command"} and value is not None}
    if args.command in {"sql", "probe"}:
        argument = {"sql": args.query}
    try:
        if args.command == "ask":   # calls.jsonl keeps the parsed questions: the API maps them to the clarify form
            argument = _clarification_argument(argument)
        result = _run(session_dir, args.command, argument, opener)
    except (ToolError, GuardError, json.JSONDecodeError) as exc:
        _log(session_dir, args.command, argument, False, str(exc)[:300],
             used_repair=isinstance(exc, AnswerNotRecorded))
        return 1, f"ERROR: {exc}"
    _log(session_dir, args.command, argument, True, _summary(args.command, result))
    return 0, result if isinstance(result, str) else json.dumps(result, default=str)


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = build_parser().parse_args(argv)
    meta_path = Path(args.session) / "session.json"
    meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
    if meta.get("data_channel"):
        from analyzer.run_channel import RunChannelError, send_command
        try:
            exit_code, output = send_command(Path(args.session), argv)
        except RunChannelError as exc:
            exit_code, output = 1, f"ERROR: {exc}"
    else:
        exit_code, output = execute(argv)
    print(output)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
