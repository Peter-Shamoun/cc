"""Analyzer interaction layer ``claude_code_cli``: the shipped analyzer configuration (bundle B1) run by local headless
Claude Code (``claude -p`` on the user's subscription, no API key), exactly as the benchmark runs it.

One question is one prepared bench session (``bench.agent_session.create_session`` + ``analyzer.harness.stamp_session``)
and one ``claude -p`` worker (``bench.claude_print_runner.worker_command``) locked to ``python3 -m analyzer.cli``. The
CLI appends every command to the session's calls.jsonl as it runs. This module tails that file and turns each line
into contract step events through ``LiveTap.observe_finished_call``, so steps stream during the run. Stop kills the
worker's process group. When the worker exits, final.json is the answer: an answer's SQL is re-executed in the
session's sandbox (the answer contract does not trust numbers from the transcript); a refusal or clarification maps
as it is.

Limits (dev and mock runs only; ``api.app.create_app`` enforces the first):
- Data scope is the bench snapshot named by ``CC_API_ANALYZER_SNAPSHOT``, not the signed-in customer's lake, so the
  app refuses this layer with any auth backend but ``DemoAuth``.
- ``escalate`` (``lossless.*``) is disallowed for the worker, and a session that escalated anyway fails closed.
- B1 has ``ask`` off, so there is no live clarification; a clarification final still maps (``needs_input``).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from analyzer.answer import AnalyzerAnswer
from analyzer.bundles import bundle
from analyzer.examples import LIVE_QUESTION_ID
from analyzer.harness import _COMMAND_TOOL, cli_prefix, config_from_meta, stamp_session
from analyzer.opener import SnapshotOpener
from analyzer.tools import ToolLayer
from bench.agent_session import create_session
from bench.claude_print_runner import ROOT, WORKER_DIRECTORY, WORKER_TIMEOUT_SECONDS, worker_command

from api.live import Cancelled, LiveTap

SHIPPED_ANALYZER_BUNDLE_ID = "B1"
#: the ``claude -p --model`` alias the shipped configuration was scored with
SHIPPED_ANALYZER_MODEL_ALIAS = "haiku"
SESSION_RUN_LABEL = "api_chat"
CALLS_POLL_SECONDS = 0.25
WORKER_STOP_GRACE_SECONDS = 2.0


class AnalyzerRunFailed(RuntimeError):
    """The worker ended without a usable final answer. The message is for the server log: no question or data."""


def start_worker_process(command: list[str], working_directory: Path, environment: dict, stdout_path: Path,
                         stderr_path: Path) -> subprocess.Popen:
    """The worker in its own process group, so Stop can kill it with every command it started."""
    with open(stdout_path, "w") as stdout_file, open(stderr_path, "w") as stderr_file:
        return subprocess.Popen(command, cwd=working_directory, env=environment, stdout=stdout_file,
                                stderr=stderr_file, stdin=subprocess.DEVNULL, start_new_session=True)


def stop_worker_process(process: subprocess.Popen) -> None:
    """SIGTERM to the worker's group now (Stop must not wait on it), SIGKILL from a thread if it lingers."""
    if process.poll() is not None:
        return
    _signal_group(process, signal.SIGTERM)

    def kill_if_still_running():
        try:
            process.wait(timeout=WORKER_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            _signal_group(process, signal.SIGKILL)
            process.wait()

    threading.Thread(target=kill_if_still_running, name=f"stop-worker-{process.pid}", daemon=True).start()


def _signal_group(process: subprocess.Popen, stop_signal: int) -> None:
    try:
        os.killpg(process.pid, stop_signal)
    except ProcessLookupError:
        pass


class CallsFileTail:
    """New complete lines of a session's calls.jsonl since the last read (a half-written last line waits)."""

    def __init__(self, calls_path: Path):
        self.calls_path = calls_path
        self.read_offset = 0

    def new_records(self) -> list[dict]:
        try:
            with open(self.calls_path, "rb") as calls_file:
                calls_file.seek(self.read_offset)
                unread = calls_file.read()
        except FileNotFoundError:
            return []
        complete = unread[:unread.rfind(b"\n") + 1]
        self.read_offset += len(complete)
        return [json.loads(line) for line in complete.splitlines() if line.strip()]


def observe_call_record(tap: LiveTap, call_record: dict) -> None:
    """One calls.jsonl line → the step events the in-process loop would emit for the same tool call."""
    command = call_record["command"]
    argument = call_record.get("argument") or {}
    tool_name = _COMMAND_TOOL.get(command, command)
    if command in ("refuse", "ask"):
        if call_record["ok"]:
            tap.observe_finished_call(tool_name, argument, None)   # sets tap.refuse_args / tap.ask_args
        return
    if not call_record["ok"]:
        tap.observe_finished_call(tool_name, argument, f"ERROR: {call_record['result']}")
        return
    if command == "answer":
        tap.observe_finished_call(tool_name, {"sql": argument.get("sql") or ""}, None)
        return
    result = call_record["result"]
    tap.observe_finished_call(tool_name, argument, json.dumps(result) if isinstance(result, dict) else "{}")


def record_worker_usage(tap: LiveTap, worker_result: dict) -> None:
    """Token counts and model time from the ``claude -p`` JSON result, in the tap's usage table."""
    for model_name, model_usage in (worker_result.get("modelUsage") or {}).items():
        row = tap.stats.models.setdefault(model_name, {"inputTokens": 0, "outputTokens": 0, "calls": 0})
        row["inputTokens"] += int(model_usage.get("inputTokens") or 0) + int(
            model_usage.get("cacheReadInputTokens") or 0) + int(model_usage.get("cacheCreationInputTokens") or 0)
        row["outputTokens"] += int(model_usage.get("outputTokens") or 0)
        row["calls"] += int(worker_result.get("num_turns") or 0)
    tap.stats.model_ms += float(worker_result.get("duration_api_ms") or 0)


@dataclass
class ClaudeCodeCliDriver:
    snapshot_path: Path
    run_label: str = SESSION_RUN_LABEL
    calls_poll_seconds: float = CALLS_POLL_SECONDS
    worker_timeout_seconds: float = WORKER_TIMEOUT_SECONDS
    start_worker: Callable[..., subprocess.Popen] = start_worker_process

    model_label = SHIPPED_ANALYZER_MODEL_ALIAS

    def prepare_session(self, question_text: str) -> Path:
        technique_config = dict(bundle(SHIPPED_ANALYZER_BUNDLE_ID).technique_config)
        session_dir = create_session(self.snapshot_path, {"id": LIVE_QUESTION_ID, "question": question_text},
                                     technique_config, SHIPPED_ANALYZER_MODEL_ALIAS, self.run_label)
        stamp_session(session_dir)
        return session_dir

    def worker_command(self, session_dir: Path) -> list[str]:
        effort = bundle(SHIPPED_ANALYZER_BUNDLE_ID).technique_config["effort"]
        no_escalation = f"Bash({cli_prefix(session_dir)} escalate:*)"
        return worker_command(str(session_dir), SHIPPED_ANALYZER_MODEL_ALIAS, effort, (no_escalation,))

    def answer(self, question_text: str, tap: LiveTap, cancelled: threading.Event) -> AnalyzerAnswer:
        """Run the question to its final answer, streaming steps into ``tap``. Raises ``Cancelled`` on Stop."""
        session_dir = self.prepare_session(question_text)
        WORKER_DIRECTORY.mkdir(exist_ok=True)
        process = self.start_worker(self.worker_command(session_dir), WORKER_DIRECTORY,
                                    {**os.environ, "PYTHONPATH": str(ROOT)}, session_dir / "claude_print.json",
                                    session_dir / "claude_print_stderr.txt")
        calls_tail = CallsFileTail(session_dir / "calls.jsonl")
        deadline = time.monotonic() + self.worker_timeout_seconds
        try:
            while process.poll() is None:
                for call_record in calls_tail.new_records():
                    observe_call_record(tap, call_record)
                if cancelled.is_set():
                    raise Cancelled()
                if time.monotonic() > deadline:
                    raise AnalyzerRunFailed(f"worker timed out after {self.worker_timeout_seconds} s")
                cancelled.wait(self.calls_poll_seconds)
            for call_record in calls_tail.new_records():
                observe_call_record(tap, call_record)
        finally:
            stop_worker_process(process)
        worker_result = _read_worker_result(session_dir / "claude_print.json")
        record_worker_usage(tap, worker_result)
        return final_answer_from_session(session_dir, worker_result)


def _read_worker_result(result_path: Path) -> dict:
    try:
        return json.loads(result_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"is_error": True, "terminal_reason": "no JSON result"}


def final_answer_from_session(session_dir: Path, worker_result: dict) -> AnalyzerAnswer:
    if (session_dir / "escalation.json").exists():
        raise AnalyzerRunFailed("the worker escalated to lossless views, which chat never grants")
    final_path = session_dir / "final.json"
    if not final_path.exists():
        raise AnalyzerRunFailed("worker ended without a final answer "
                                f"({worker_result.get('terminal_reason') or worker_result.get('subtype')})")
    final = json.loads(final_path.read_text())
    usage = {"worker_seconds": worker_result.get("duration_ms"), "turns": worker_result.get("num_turns")}
    if final["kind"] == "refusal":
        return AnalyzerAnswer(kind="refusal", text=final["text"], refused=True, model=SHIPPED_ANALYZER_MODEL_ALIAS,
                              usage=usage)
    if final["kind"] == "clarification":
        return AnalyzerAnswer(kind="clarification", text=final["text"], model=SHIPPED_ANALYZER_MODEL_ALIAS,
                              usage=usage)
    if final["kind"] != "answer":
        raise AnalyzerRunFailed(f"unknown final kind {final['kind']!r}")
    meta = json.loads((session_dir / "session.json").read_text())
    tools = ToolLayer(SnapshotOpener(meta["snapshot_path"]), config_from_meta(meta, for_subagent=True))
    digest = tools.run_sql(final["sql"], charge=False, include_all_rows=True)
    return AnalyzerAnswer(kind="answer", text=final["text"], sql=final["sql"], rows=digest["all_rows"],
                          columns=digest["columns"], warnings=list(final.get("result_warnings") or []),
                          model=SHIPPED_ANALYZER_MODEL_ALIAS, usage=usage)
