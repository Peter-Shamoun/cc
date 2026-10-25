"""Adapter from the A02 protocol onto bench.eval_subagents.

`eval_subagents prepare` only accepts technique flags that bench.agent_cli already
knows. This module's config turns those flags on for the CLI surface the driver
uses (read-only sql, error hints, result warnings, goal briefing, low effort).
The repair policy is not one of those flags. `drive_manifest` runs this package's
protocol into the prepared sessions by shelling out to `python -m bench.agent_cli`,
which is the same guarded snapshot path `eval_subagents score` re-executes.
That path is the bench form of ccsim.query.open_session: sem.* unless the question
escalates, one read-only SELECT, row limit, no external access.

Nothing here calls a paid model API. `drive` requires a scripted tool-call JSON file.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from bench.agents import Answer

from .backend import ScriptedBackend
from .protocol import (
    MAX_REPAIRS,
    MAX_SAMPLE_CALLS,
    ExecutionResult,
    run_question,
)

CONFIG_NAME = "a02_tool_loop"
# 3 samples + 1 draft + 2 repairs = 6 investigative sql calls, plus headroom for a
# failed call that agent_cli still counts against max_tool_calls. context and answer do not count.
TECHNIQUE_CONFIG = {
    "glossary": "basic",
    "data_dictionary": False,
    "data_profile": False,
    "enable_sql": True,
    "enable_metrics": False,
    "enable_lookup": False,
    "enable_examples": False,
    "enable_ask": False,
    "sql_error_hints": True,
    "result_checks": True,
    "context_style": "goal",
    "effort": "low",
    "max_tool_calls": MAX_SAMPLE_CALLS + 1 + MAX_REPAIRS + 2,
}

_PACKAGE_DIR = Path(__file__).resolve().parent
CONFIG_PATH = _PACKAGE_DIR / "eval_subagents_config.json"


def configs_document() -> dict:
    return {CONFIG_NAME: dict(TECHNIQUE_CONFIG)}


class ToolsExecutor:
    """In-process executor for bench.eval's Tools. SQL goes through Tools.sql, which calls bench.guard.guard."""

    def __init__(self, tools):
        self.tools = tools
        self.escalate = bool(getattr(tools, "escalate", False))

    def describe(self) -> str:
        return self.tools.describe()

    def run_sql(self, sql: str) -> ExecutionResult:
        try:
            columns, rows = self.tools.sql(sql)
        except Exception as exc:  # noqa: BLE001 - the model has to see the guard or binder error
            return ExecutionResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:500])
        return ExecutionResult(ok=True, columns=list(columns), rows=list(rows), row_count=len(rows))

    def submit_answer(self, sql: str, text: str) -> ExecutionResult:
        try:
            columns, rows = self.tools.sql(sql, _final=True)
        except Exception as exc:  # noqa: BLE001
            return ExecutionResult(ok=False, error=f"{type(exc).__name__}: {exc}"[:500])
        return ExecutionResult(ok=True, columns=list(columns), rows=list(rows), row_count=len(rows))

    def submit_refusal(self, reason: str) -> str | None:
        return None


class AgentCliExecutor:
    """Drive one prepared session through bench.agent_cli, so calls.jsonl and final.json match the scorer.

    `runner`, when set, replaces the subprocess. Tests use it. The default runner is the real CLI,
    which opens the snapshot the way a scored subagent does.
    """

    def __init__(self, session_dir: Path, runner=None):
        self.session_dir = Path(session_dir)
        self.runner = runner
        meta = json.loads((self.session_dir / "session.json").read_text())
        self.escalate = bool(meta.get("escalate", False))

    def _invoke(self, args: list[str]) -> tuple[int, str]:
        command = [sys.executable, "-m", "bench.agent_cli", "--session", str(self.session_dir), *args]
        if self.runner is not None:
            return self.runner(command)
        completed = subprocess.run(command, check=False, capture_output=True, text=True)
        output = completed.stdout if completed.stdout else completed.stderr
        return completed.returncode, output

    def describe(self) -> str:
        code, output = self._invoke(["context"])
        if code != 0:
            raise RuntimeError(output.strip()[:500] or "context failed")
        return output

    def run_sql(self, sql: str) -> ExecutionResult:
        code, output = self._invoke(["sql", sql])
        if code != 0:
            message = output.strip()
            if message.startswith("ERROR:"):
                message = message[len("ERROR:"):].strip()
            return ExecutionResult(ok=False, error=message[:500])
        payload = json.loads(output)
        rows = payload.get("rows") or []
        return ExecutionResult(
            ok=True,
            columns=list(payload.get("columns") or []),
            rows=rows,
            row_count=int(payload.get("row_count", len(rows))),
            error="",
        )

    def submit_answer(self, sql: str, text: str) -> ExecutionResult:
        code, output = self._invoke(["answer", "--sql", sql, "--text", text])
        if code != 0:
            message = output.strip()
            if message.startswith("ERROR:"):
                message = message[len("ERROR:"):].strip()
            return ExecutionResult(ok=False, error=message[:500])
        return ExecutionResult(ok=True)

    def submit_refusal(self, reason: str) -> str | None:
        code, output = self._invoke(["refuse", reason])
        if code != 0:
            return output.strip()[:500] or "refuse failed"
        return None


class A02ToolLoopAgent:
    """bench.agents.Answer adapter. Construct with a backend; the no-arg constructor does not call an API.

    bench.eval's loader calls this with no arguments. answer() then raises, unless A02_SCRIPTED_STEPS
    points at a local JSON script. That is a debug hook for one scripted question, not a model.
    """

    def __init__(self, backend=None):
        self.backend = backend

    def _backend(self):
        if self.backend is not None:
            return self.backend
        script_path = __import__("os").environ.get("A02_SCRIPTED_STEPS")
        if script_path:
            return ScriptedBackend(json.loads(Path(script_path).read_text()))
        raise RuntimeError(
            "A02ToolLoopAgent has no backend. Pass ScriptedBackend, or set A02_SCRIPTED_STEPS to a JSON file. "
            "This package does not call a paid model API."
        )

    def answer(self, question, tools) -> Answer:
        return run_question(question, ToolsExecutor(tools), self._backend(), escalate=bool(tools.escalate))


def drive_session(session_dir: Path, backend, runner=None) -> Answer:
    """Run the protocol into one prepared session directory."""
    session_dir = Path(session_dir)
    meta = json.loads((session_dir / "session.json").read_text())
    if (session_dir / "final.json").exists():
        raise FileExistsError(f"{session_dir / 'final.json'} already exists")
    answer = run_question(
        meta["question"],
        AgentCliExecutor(session_dir, runner=runner),
        backend,
        escalate=bool(meta.get("escalate", False)),
    )
    (session_dir / "a02_trace.json").write_text(json.dumps({
        "question_id": meta.get("question_id"),
        "refused": answer.refused,
        "sql": answer.sql,
        "text": answer.text,
        "usage": answer.usage,
        "trace": answer.trace,
    }, indent=1, default=str))
    return answer


def drive_manifest(manifest_path: Path, backend_for_entry, runner=None) -> list[Answer]:
    """Run every a02_tool_loop entry in a manifest written by bench.eval_subagents prepare.

    backend_for_entry(entry, meta) returns the backend for that session. Other config names are skipped
    so a mixed manifest can carry arms this driver does not own.
    """
    manifest = json.loads(Path(manifest_path).read_text())
    answers = []
    for entry in manifest["entries"]:
        if entry.get("config") != CONFIG_NAME:
            continue
        session_dir = Path(entry["session"])
        meta = json.loads((session_dir / "session.json").read_text())
        answers.append(drive_session(session_dir, backend_for_entry(entry, meta), runner=runner))
    return answers


def write_config(path: Path) -> Path:
    path = Path(path)
    path.write_text(json.dumps(configs_document(), indent=1) + "\n")
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="A02 tool loop, scripted driver for bench.eval_subagents")
    sub = parser.add_subparsers(dest="command", required=True)
    write = sub.add_parser("write-config", help="write the eval_subagents configs.json document")
    write.add_argument("path")
    drive = sub.add_parser("drive", help="run prepared sessions from a scripted tool-call file")
    drive.add_argument("manifest")
    drive.add_argument("--script", required=True, help="JSON list of {tool, arguments, text?}. Required so this command cannot call a model API.")
    args = parser.parse_args(argv)
    if args.command == "write-config":
        print(write_config(Path(args.path)))
        return 0
    script = json.loads(Path(args.script).read_text())
    answers = drive_manifest(Path(args.manifest), lambda entry, meta: ScriptedBackend(script))
    print(json.dumps({"sessions": len(answers), "refused": sum(a.refused for a in answers),
                      "answered": sum(bool(a.sql) for a in answers)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
