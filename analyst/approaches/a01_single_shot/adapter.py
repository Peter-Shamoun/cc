"""Run A01 as a config that ``bench/eval_subagents.py`` can prepare and score.

``technique_config`` is a legal ``bench/agent_cli.py`` settings dict, so ``prepare`` accepts it.
The CLI briefing that ``prepare`` writes is an exploratory loop (context, then SQL, then a
separate answer). That loop is not A01. ``prepare_run`` stamps the manifest so the orchestrator
calls ``drive_session`` instead of following the briefing.

``drive_session`` makes the one model call, executes at most once, and writes ``final.json``.
``python -m bench.eval_subagents score RUN`` then re-executes that same statement. The re-execution
is the harness scorer, not a repair: the model is not shown the error and is not called again.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
from bench.agent_session import SESSIONS_ROOT

from .executor import SnapshotQueryExecutor
from .protocol import Outcome, run_single_shot

APPROACH_ID = "a01_single_shot"
DRIVER = "analyst.approaches.a01_single_shot.adapter:drive_session"


def technique_config() -> dict:
    """Settings ``create_session`` accepts. ``max_tool_calls`` 1 is only a backstop if a CLI agent is pointed at the session.

    The protocol is ``drive_session``. A CLI agent that calls context, looks at rows, and submits
    a different statement is not A01, even with these settings.
    """
    config = {
        **DEFAULT_TECHNIQUE_CONFIG,
        "glossary": "basic",
        "data_dictionary": True,
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
        "max_tool_calls": 1,
    }
    unknown = set(config) - set(DEFAULT_TECHNIQUE_CONFIG)
    if unknown:
        raise ValueError(f"unknown technique settings: {sorted(unknown)}")
    return config


def as_eval_configs() -> dict:
    """The ``configs.json`` object ``python -m bench.eval_subagents prepare`` reads."""
    return {APPROACH_ID: technique_config()}


def assert_session_is_a01(raw_technique_config: dict) -> None:
    unknown = set(raw_technique_config) - set(DEFAULT_TECHNIQUE_CONFIG)
    if unknown:
        raise ValueError(f"unknown technique settings: {sorted(unknown)}")
    merged = {**DEFAULT_TECHNIQUE_CONFIG, **raw_technique_config}
    expected = technique_config()
    if merged != expected:
        mismatches = [f"{key}={merged[key]!r}" for key in sorted(expected) if merged[key] != expected[key]]
        raise ValueError("session is not the a01 single-shot config: " + ", ".join(mismatches))


def orchestrator_instruction(session: str, question_id: str) -> str:
    return (
        "Approach a01_single_shot. Do not run bench/agent_cli.py and do not write SQL in this step.\n"
        "Execute the session with one call to "
        f"{DRIVER} passing the session directory and a model backend. "
        "The backend is allowed one completion. That completion may submit one SELECT, "
        "which is executed once, and that execution is the answer. There is no repair and no exploration.\n"
        f"Session: {session}\n"
        f"Question id: {question_id}\n"
        "Then score with: python -m bench.eval_subagents score <run>\n"
    )


def stamp_manifest(manifest: dict) -> dict:
    """Replace each entry's CLI briefing with the instruction to drive A01. Does not call a model."""
    manifest = dict(manifest)
    manifest["approach"] = APPROACH_ID
    manifest["protocol"] = "single_shot"
    manifest["driver"] = DRIVER
    stamped = []
    for entry in manifest["entries"]:
        updated = dict(entry)
        updated["approach"] = APPROACH_ID
        updated["driver"] = DRIVER
        updated["prompt"] = orchestrator_instruction(entry["session"], entry["question_id"])
        stamped.append(updated)
    manifest["entries"] = stamped
    return manifest


def prepare_run(run_label: str, models: list[str], repeats: int = 1, only: str | None = None,
                snapshot_path: Path | None = None) -> Path:
    """Create the eval_subagents run, then stamp it. ``snapshot_path`` is required so this does not build a dataset."""
    if snapshot_path is None:
        raise ValueError("snapshot_path is required; a01 does not build a dataset")
    from bench.eval_subagents import prepare

    manifest_path = prepare(run_label, as_eval_configs(), models, repeats, only, Path(snapshot_path))
    manifest = json.loads(manifest_path.read_text())
    manifest_path.write_text(json.dumps(stamp_manifest(manifest), indent=1))
    return manifest_path


def _write_outcome(session_dir: Path, outcome: Outcome) -> None:
    trace = {
        "approach": APPROACH_ID,
        "protocol": "single_shot",
        "kind": outcome.kind,
        "model_calls": outcome.model_calls,
        "executions": outcome.executions,
        "error": outcome.error,
        "usage": outcome.usage,
        "tool_calls": outcome.tool_calls,
        "effort": "low",
    }
    (session_dir / "a01_trace.json").write_text(json.dumps(trace, indent=1))
    if outcome.kind == "refusal":
        (session_dir / "final.json").write_text(json.dumps({
            "kind": "refusal", "text": outcome.text, "approach": APPROACH_ID,
        }))
        _log_call(session_dir, "refuse", {"reason": outcome.text}, True, "ok")
        return
    if outcome.kind in ("answer", "execution_error"):
        (session_dir / "final.json").write_text(json.dumps({
            "kind": "answer", "sql": outcome.sql, "text": outcome.text, "via": "sql", "approach": APPROACH_ID,
        }))
        ok = outcome.kind == "answer"
        summary = "ok" if ok else outcome.error
        _log_call(session_dir, "sql", {"query": outcome.sql}, ok, summary)
        return
    _log_call(session_dir, "protocol", {"tool_calls": outcome.tool_calls}, False, outcome.error or "protocol error")


def _log_call(session_dir: Path, command: str, argument, ok: bool, result) -> None:
    with open(session_dir / "calls.jsonl", "a") as handle:
        handle.write(json.dumps({
            "t": round(time.time(), 3), "command": command, "argument": argument, "ok": ok, "result": result,
        }, default=str) + "\n")


def drive_session(session_dir: Path, backend, executor=None) -> Outcome:
    """One completion and at most one execution for a session ``prepare`` already created.

    Refuses to run a session whose technique config is not A01, and refuses to run a session
    that already has an answer, so a second call cannot repair or replace the first statement.
    """
    session_dir = Path(session_dir)
    if (session_dir / "final.json").exists() or (session_dir / "calls.jsonl").exists():
        raise ValueError(f"{session_dir} already has a driven result; a01 does not repair or re-run")
    meta = json.loads((session_dir / "session.json").read_text())
    assert_session_is_a01(meta["technique_config"])
    if executor is None:
        executor = SnapshotQueryExecutor(Path(meta["snapshot_path"]), escalate=bool(meta.get("escalate", False)))
    outcome = run_single_shot(meta["question"], backend, executor)
    if outcome.model_calls != 1:
        raise RuntimeError(f"a01 made {outcome.model_calls} model calls")
    if outcome.executions > 1:
        raise RuntimeError(f"a01 executed {outcome.executions} statements")
    _write_outcome(session_dir, outcome)
    return outcome


def drive_run(run_label: str, backend_for_model) -> list[Outcome]:
    """Drive every manifest entry. ``backend_for_model(model_label)`` must not be called by this module with a live client."""
    manifest_path = SESSIONS_ROOT / run_label / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("approach") != APPROACH_ID:
        raise ValueError(f"{manifest_path} is not an {APPROACH_ID} manifest; call prepare_run or stamp_manifest first")
    outcomes = []
    for entry in manifest["entries"]:
        outcomes.append(drive_session(Path(entry["session"]), backend_for_model(entry["model"])))
    return outcomes
