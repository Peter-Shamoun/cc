"""Run A03 inside the existing subagent harness.

``bench.eval_subagents.prepare`` only accepts technique flags the stock CLI knows.
This adapter prepares that run, then replaces each manifest prompt with the metric-spec
briefing. Scoring is unchanged: ``python -m bench.eval_subagents score RUN`` re-executes
the SQL in final.json.

The orchestrator must send the manifest prompt. ``bench.agent_cli briefing`` still prints
the stock SQL briefing and would bypass the spec-first gate.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG  # noqa: E402
from bench.dataset import open_snapshot  # noqa: E402
from bench.guard import GuardError, guard  # noqa: E402

from .compiler import CannotExpress, SpecError, compile_spec  # noqa: E402
from .prompts import build_prompt, catalog_text, tool_spec  # noqa: E402

# Screen-2 base, plus the spec tool in the rewritten prompt. Metrics stays off so the
# stock CLI does not advertise the other compiler. SQL stays on only so a session file
# is a legal technique config; the prompt's commands are this module's CLI.
TECHNIQUE_CONFIG = {
    "glossary": "basic",
    "data_dictionary": False,
    "data_profile": False,
    "enable_sql": True,
    "enable_metrics": False,
    "enable_lookup": False,
    "enable_examples": False,
    "enable_ask": False,
    "sql_error_hints": False,
    "result_checks": False,
    "context_style": "goal",
    "effort": "low",
    "max_tool_calls": 8,
}

# Prices are USD per million tokens, from research/technique_factors.yaml (Claude 5.5, prompts <= 100k).
# Cache reads are 0.1x the input price. These are list prices, not a measured invoice.
MODEL_PRICES_USD_PER_MILLION = {
    "haiku": {"input": 0.10, "output": 0.50},
    "sonnet": {"input": 2.00, "output": 10.00},
}
CACHE_READ_MULTIPLIER = 0.1

# A typical question after the cached prefix: the question, the spec the model writes,
# a short tool result, and a one-paragraph answer. Thinking tokens are not included;
# low effort adds output tokens on top of this floor. A SQL fallback adds one more turn.
TYPICAL_FRESH_INPUT_TOKENS = 700
TYPICAL_OUTPUT_TOKENS = 500
FALLBACK_EXTRA_INPUT_TOKENS = 400
FALLBACK_EXTRA_OUTPUT_TOKENS = 350

ROWS_SHOWN = 20


class CliError(Exception):
    pass


def technique_config() -> dict:
    unknown = set(TECHNIQUE_CONFIG) - set(DEFAULT_TECHNIQUE_CONFIG)
    if unknown:
        raise RuntimeError(f"technique config has keys the harness rejects: {sorted(unknown)}")
    return dict(TECHNIQUE_CONFIG)


def configs() -> dict:
    """Body of a configs.json for ``python -m bench.eval_subagents prepare``."""
    return {"a03_metric_spec": technique_config()}


def estimate_question_cost(model: str, prefix_tokens: int, *, cache_warm: bool, sql_fallback: bool) -> float:
    """USD for one question. prefix_tokens is the cached catalog and instructions."""
    if model not in MODEL_PRICES_USD_PER_MILLION:
        raise ValueError(f"unknown model {model!r}")
    prices = MODEL_PRICES_USD_PER_MILLION[model]
    fresh = TYPICAL_FRESH_INPUT_TOKENS + (FALLBACK_EXTRA_INPUT_TOKENS if sql_fallback else 0)
    output = TYPICAL_OUTPUT_TOKENS + (FALLBACK_EXTRA_OUTPUT_TOKENS if sql_fallback else 0)
    if cache_warm:
        input_tokens_priced = prefix_tokens * CACHE_READ_MULTIPLIER + fresh
    else:
        input_tokens_priced = prefix_tokens + fresh
    return input_tokens_priced * prices["input"] / 1_000_000 + output * prices["output"] / 1_000_000


def prefix_tokens(question: str = "How many claims were denied?") -> int:
    """Rough token count: characters of the stable briefing divided by 4."""
    text = build_prompt(question, "/session", escalate=False)
    stable = text.split("User question:", 1)[0]
    return max(1, len(stable) // 4)


def rewrite_manifest_prompts(manifest_path: Path) -> Path:
    """Point a prepare() manifest at the A03 briefing. Score still reads final.json."""
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["entries"]:
        meta = json.loads((Path(entry["session"]) / "session.json").read_text())
        entry["prompt"] = build_prompt(meta["question"], entry["session"], meta.get("escalate", False))
        entry["approach"] = "a03_metric_spec"
    manifest["approach"] = "a03_metric_spec"
    manifest_path.write_text(json.dumps(manifest, indent=1))
    return manifest_path


def prepare_run(run_label: str, models: list[str], repeats: int = 1, only: str | None = None,
                snapshot_path: str | None = None) -> Path:
    """Create a subagent run the stock scorer can grade, with A03 prompts."""
    from bench.eval_subagents import prepare

    manifest_path = prepare(run_label, configs(), models, repeats, only,
                            Path(snapshot_path) if snapshot_path else None)
    return rewrite_manifest_prompts(manifest_path)


def sql_fallback_allowed(calls: list[dict]) -> bool:
    """True when a spec in this session was rejected as inexpressible, not merely malformed."""
    return any(
        c.get("command") in ("metrics", "spec") and not c.get("ok") and "cannot_express" in str(c.get("result", ""))
        for c in calls)


def run_spec(connection, spec: dict, escalate: bool = False) -> dict:
    """Compile and, when the connection is not None, execute. CannotExpress is returned, not raised."""
    try:
        compiled = compile_spec(spec)
    except SpecError as exc:
        return {"status": "spec_error", "message": str(exc)}
    except CannotExpress as exc:
        return {"status": "cannot_express", "reason": exc.reason, "hint": exc.hint}
    guarded = guard(compiled.sql, 1000, escalate)
    if connection is None:
        return {"status": "compiled", "sql": compiled.sql, "notes": compiled.notes,
                "output_columns": compiled.output_columns}
    cursor = connection.execute(guarded)
    columns = [desc[0] for desc in cursor.description]
    rows = cursor.fetchall()
    payload = {
        "status": "compiled",
        "sql": compiled.sql,
        "notes": list(compiled.notes),
        "output_columns": compiled.output_columns,
        "columns": columns,
        "row_count": len(rows),
        "rows": [list(row) for row in rows[:ROWS_SHOWN]],
        "truncated": len(rows) > ROWS_SHOWN,
    }
    if not rows:
        payload["notes"].append("the query returned no rows: a filter value may not match the stored spelling")
    return payload


def _read_calls(session_dir: Path) -> list[dict]:
    path = session_dir / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _log(session_dir: Path, command: str, argument, ok: bool, result) -> None:
    with open(session_dir / "calls.jsonl", "a") as handle:
        handle.write(json.dumps({
            "t": round(time.time(), 3), "command": command, "argument": argument, "ok": ok, "result": result,
        }, default=str) + "\n")


def _budget_used(session_dir: Path, meta: dict) -> None:
    limit = {**DEFAULT_TECHNIQUE_CONFIG, **meta.get("technique_config", {})}["max_tool_calls"]
    used = sum(1 for call in _read_calls(session_dir) if call["command"] in ("metrics", "sql"))
    if used >= limit:
        raise CliError(f"tool-call budget of {limit} used up: answer or refuse now")


def _open(session_dir: Path):
    meta = json.loads((session_dir / "session.json").read_text())
    connection = open_snapshot(Path(meta["snapshot_path"]), escalate=meta.get("escalate", False))
    return meta, connection


def _submit(session_dir: Path, sql: str, text: str, via: str, escalate: bool) -> dict:
    final_path = session_dir / "final.json"
    if final_path.exists():
        raise CliError("an answer was already submitted")
    guard(sql, 1000, escalate)
    final_path.write_text(json.dumps({"kind": "answer", "sql": sql, "text": text, "via": via}))
    return {"recorded": True, "via": via}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A03 metric-spec agent CLI")
    parser.add_argument("--session", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("catalog")
    sub.add_parser("tool-spec")
    spec_cmd = sub.add_parser("spec")
    spec_cmd.add_argument("request_json")
    answer = sub.add_parser("answer")
    answer.add_argument("--spec")
    answer.add_argument("--sql")
    answer.add_argument("--text", default="")
    refuse = sub.add_parser("refuse")
    refuse.add_argument("reason")
    args = parser.parse_args(argv)
    session_dir = Path(args.session)
    meta = json.loads((session_dir / "session.json").read_text())
    escalate = bool(meta.get("escalate", False))

    if args.command == "catalog":
        print(catalog_text())
        return 0
    if args.command == "tool-spec":
        print(json.dumps(tool_spec(), indent=1))
        return 0
    if args.command == "refuse":
        final_path = session_dir / "final.json"
        if final_path.exists():
            print("ERROR: an answer was already submitted")
            return 1
        final_path.write_text(json.dumps({"kind": "refusal", "text": args.reason}))
        _log(session_dir, "refuse", {"reason": args.reason}, True, "ok")
        print(json.dumps({"recorded": True}))
        return 0

    try:
        if args.command == "spec":
            _budget_used(session_dir, meta)
            payload = _execute_spec(session_dir, meta, args.request_json, escalate)
        else:
            payload = _execute_answer(session_dir, meta, args.spec, args.sql, args.text, escalate)
    except CliError as exc:
        print(f"ERROR: {exc}")
        return 1
    print(json.dumps(payload, default=str))
    return 0


def _execute_spec(session_dir: Path, meta: dict, request_json: str, escalate: bool) -> dict:
    del meta
    try:
        spec = json.loads(request_json)
    except json.JSONDecodeError as exc:
        _log(session_dir, "metrics", {"approach": "a03_metric_spec"}, False, f"spec: {exc}"[:300])
        raise CliError(f"spec: {exc}") from None
    try:
        connection = _open(session_dir)[1]
    except Exception as exc:  # noqa: BLE001 - the model sees a readable failure
        _log(session_dir, "metrics", {"approach": "a03_metric_spec", "spec": spec}, False, f"spec: {exc}"[:300])
        raise CliError(f"spec: {exc}") from None
    payload = run_spec(connection, spec, escalate)
    if payload["status"] == "spec_error":
        _log(session_dir, "metrics", {"approach": "a03_metric_spec", "spec": spec}, False,
             f"spec: {payload['message']}"[:300])
        raise CliError(f"spec: {payload['message']}")
    if payload["status"] == "cannot_express":
        message = payload["reason"] if not payload.get("hint") else f"{payload['reason']} Hint: {payload['hint']}"
        _log(session_dir, "metrics", {"approach": "a03_metric_spec", "spec": spec}, False,
             f"cannot_express: {message}"[:300])
        raise CliError(f"cannot_express: {message}")
    _log(session_dir, "metrics", {"approach": "a03_metric_spec", "spec": spec}, True,
         {"status": "compiled", "row_count": payload["row_count"]})
    return payload


def _execute_answer(session_dir: Path, meta: dict, spec_json: str | None, sql: str | None, text: str,
                    escalate: bool) -> dict:
    if bool(spec_json) == bool(sql):
        raise CliError("give exactly one of --spec or --sql")
    if spec_json:
        try:
            spec = json.loads(spec_json)
        except json.JSONDecodeError as exc:
            raise CliError(f"spec: {exc}") from None
        try:
            compiled = compile_spec(spec)
        except SpecError as exc:
            _log(session_dir, "metrics", {"approach": "a03_metric_spec", "spec": spec}, False, f"spec: {exc}"[:300])
            raise CliError(f"spec: {exc}") from None
        except CannotExpress as exc:
            _log(session_dir, "metrics", {"approach": "a03_metric_spec", "spec": spec}, False,
                 f"cannot_express: {exc}"[:300])
            raise CliError(f"cannot_express: {exc}") from None
        _log(session_dir, "metrics", {"approach": "a03_metric_spec", "spec": spec}, True, {"status": "compiled"})
        recorded = _submit(session_dir, compiled.sql, text, "a03_metric_spec", escalate)
        _log(session_dir, "answer", {"via": "a03_metric_spec"}, True, "ok")
        return recorded
    if not sql_fallback_allowed(_read_calls(session_dir)):
        raise CliError("answer --sql is available only after a spec returns cannot_express")
    try:
        guard(sql, 1000, escalate)
    except GuardError as exc:
        _log(session_dir, "sql", {"approach": "a03_metric_spec", "query": sql}, False, str(exc)[:300])
        raise CliError(str(exc)) from None
    _log(session_dir, "sql", {"approach": "a03_metric_spec", "query": sql}, True, {"status": "sql_fallback"})
    recorded = _submit(session_dir, sql, text, "sql_fallback", escalate)
    _log(session_dir, "answer", {"via": "sql_fallback"}, True, "ok")
    return recorded


if __name__ == "__main__":
    sys.exit(main())
