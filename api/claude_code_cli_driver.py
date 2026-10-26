"""Analyzer interaction layer ``claude_code_cli``: the shipped analyzer configuration (bundle B1) run by local headless
Claude Code (``claude -p`` on the user's subscription, no API key), exactly as the benchmark runs it.

One question is one prepared bench session (``bench.agent_session.create_session`` + ``analyzer.harness.stamp_session``)
and one ``claude -p`` worker (``bench.claude_print_runner.worker_command``) locked to ``python3 -m analyzer.cli``. The
CLI appends every command to the session's calls.jsonl as it runs. This module tails that file and turns each line
into contract step events through ``LiveTap.observe_finished_call``, so steps stream during the run. Stop kills the
worker's process group. When the worker exits, final.json is the answer: an answer's SQL is re-executed in the
session's sandbox (the answer contract does not trust numbers from the transcript); a refusal or clarification maps
as it is.

Data scope:
- With ``DemoAuth`` the CLI queries the bench snapshot named by ``CC_API_ANALYZER_SNAPSHOT`` itself (as the bench does).
- With a real auth backend (``SimAuth``) the run queries the signed-in customer's lake: ``answer`` gets the request's
  opener (``QueryPathOpener`` → ``ccsim.query.open_session``, customer from the verified token only, wrapped by
  ``CancellableOpener`` and ``ReasonedEscalationOpener``). This process opens that reader session once per run
  (``RunReaderSession``) and serves the worker's ``analyzer.cli`` commands on a run-scoped unix socket
  (``analyzer.run_channel``): the CLI sends its argv and prints what comes back, so credentials and the connection
  never leave this process and the worker cannot name another customer. The snapshot is then only the TRAIN example
  library and playbook source of the briefing; no snapshot data is opened.

Session folders hold the question text: the app removes a run's folder once the run's message is persisted
(``discard_session``), and ``sweep_expired_sessions`` removes any left behind by a crash.

Limits (dev and mock runs only):
- ``escalate "<reason>"`` re-opens the run's reader session with ``lossless.*`` (same customer, audited with the
  reason by the request's opener). The worker's briefing lists only ``sem.*`` until then.
- B1 has ``ask`` off, so there is no live clarification; a clarification final still maps (``needs_input``).
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from analyzer.answer import AnalyzerAnswer
from analyzer.bundles import bundle
from analyzer.cli import load_escalated
from analyzer.examples import LIVE_QUESTION_ID
from analyzer.harness import _COMMAND_TOOL, cli_prefix, config_from_meta, stamp_session
from analyzer.opener import SessionHandle, SnapshotOpener
from analyzer.run_channel import DATA_CHANNEL_KIND, RunChannel
from analyzer.tools import ToolLayer
from bench import agent_session
from bench.agent_session import create_session
from bench.claude_print_runner import ROOT, WORKER_DIRECTORY, WORKER_TIMEOUT_SECONDS, worker_command

from api.live import Cancelled, LiveTap

SHIPPED_ANALYZER_BUNDLE_ID = "B1"
#: the ``claude -p --model`` alias the shipped configuration was scored with
SHIPPED_ANALYZER_MODEL_ALIAS = "haiku"
SESSION_RUN_LABEL = "api_chat"
CALLS_POLL_SECONDS = 0.25
WORKER_STOP_GRACE_SECONDS = 2.0
#: a run's reader session is reopened (new STS credentials) once it is this old; ccsim's credentials last 900 s
READER_SESSION_REOPEN_SECONDS = 600.0
#: session folders older than this are removed by the sweep (a finished run's folder goes at once)
SESSION_RETENTION_SECONDS = 600.0


class RunReaderSession:
    """One run's reader sessions: opened through the request's opener on first use, reused by every command of the
    run, reopened when one is ``READER_SESSION_REOPEN_SECONDS`` old. ``open_escalated(reason)`` (the ``escalate``
    command) goes to the request's opener, which audits it; from then on the run is escalated (``escalated``), every
    later command restores the escalated session, and the non-escalated one is dropped. The escalation state lives
    here in the API process, never in a file the worker could write."""

    def __init__(self, opener, reopen_seconds: float = READER_SESSION_REOPEN_SECONDS, clock=time.monotonic):
        self.opener = opener
        self.reopen_seconds = reopen_seconds
        self.clock = clock
        self.handles: dict[bool, tuple[SessionHandle, float]] = {}
        self.opens = 0
        self.escalated = False
        self._lock = threading.Lock()

    @property
    def handle(self) -> SessionHandle | None:
        current = self.handles.get(self.escalated)
        return current[0] if current else None

    def open(self, escalate: bool) -> SessionHandle:
        with self._lock:
            current = self.handles.get(escalate)
            if current is None or self.clock() - current[1] >= self.reopen_seconds:
                current = self.handles[escalate] = (self.opener.open(escalate), self.clock())
                self.opens += 1
            return current[0]

    def open_escalated(self, reason: str) -> SessionHandle:
        with self._lock:
            open_escalated = getattr(self.opener, "open_escalated", None)
            handle = open_escalated(reason) if open_escalated is not None else self.opener.open(True)
            self.handles = {True: (handle, self.clock())}   # the escalating command closes the sem.* session
            self.opens += 1
            self.escalated = True
            return handle


class AnalyzerRunFailed(RuntimeError):
    """The worker ended without a usable final answer. The message is for the server log: no question or data."""


def start_worker_process(command: list[str], working_directory: Path, environment: dict, stdout_path: Path,
                         stderr_path: Path) -> subprocess.Popen:
    """The worker in its own process group, so Stop can kill it with every command it started."""
    with open(stdout_path, "w") as stdout_file, open(stderr_path, "w") as stderr_file:
        return subprocess.Popen(command, cwd=working_directory, env=environment, stdout=stdout_file,
                                stderr=stderr_file, stdin=subprocess.DEVNULL, start_new_session=True)


#: what the ``claude -p`` worker inherits: enough for the subscription login (kept under HOME), the shell its Bash
#: tool runs and the egress proxy. Anything else (model API keys, Bedrock/Vertex switches, AWS credentials, the
#: API's transcript and key settings) stays in the API process.
WORKER_ENVIRONMENT_NAMES = frozenset({
    "PATH", "HOME", "USER", "LOGNAME", "SHELL", "TERM", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "TMPDIR",
    "CLAUDE_CONFIG_DIR", "HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy",
    "SSL_CERT_FILE", "NODE_EXTRA_CA_CERTS", "REQUESTS_CA_BUNDLE"})
#: never passed, even if a later edit adds them to the allowlist
WORKER_ENVIRONMENT_FORBIDDEN_PREFIXES = ("ANTHROPIC_", "CLAUDE_CODE_USE_", "AWS_", "CC_TRANSCRIPT_", "CC_API_")


def worker_environment(source_environment: dict) -> dict:
    environment = {name: value for name, value in source_environment.items() if name in WORKER_ENVIRONMENT_NAMES
                   and not name.startswith(WORKER_ENVIRONMENT_FORBIDDEN_PREFIXES)}
    environment["PYTHONPATH"] = str(ROOT)
    return environment


def stop_worker_process(process: subprocess.Popen) -> None:
    """SIGTERM to the worker's group now (Stop must not wait on it), SIGKILL from a thread if it lingers. The group
    is signalled even when the worker itself has exited: a command it started may still be running."""
    _signal_group(process, signal.SIGTERM)

    def kill_if_still_running():
        try:
            process.wait(timeout=WORKER_STOP_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            pass
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
    #: True with a real auth backend: queries go through the request's opener over the run channel
    queries_signed_in_customer: bool = False

    def __post_init__(self):
        self.live_workers: set[subprocess.Popen] = set()
        self._workers_lock = threading.Lock()

    def shutdown(self) -> None:
        """Kill every worker still running (the API is stopping): they run in their own process groups."""
        with self._workers_lock:
            workers = list(self.live_workers)
        for process in workers:
            stop_worker_process(process)

    model_label = SHIPPED_ANALYZER_MODEL_ALIAS

    def prepare_session(self, question_text: str, data_channel: bool = False) -> Path:
        technique_config = dict(bundle(SHIPPED_ANALYZER_BUNDLE_ID).technique_config)
        session_dir = create_session(self.snapshot_path, {"id": LIVE_QUESTION_ID, "question": question_text},
                                     technique_config, SHIPPED_ANALYZER_MODEL_ALIAS, self.run_label)
        stamp_session(session_dir)
        if data_channel:
            meta_path = session_dir / "session.json"
            meta = json.loads(meta_path.read_text())
            meta["data_channel"] = DATA_CHANNEL_KIND     # the CLI must use the run channel, never the snapshot
            meta_path.write_text(json.dumps(meta, indent=1))
        return session_dir

    def sessions_root(self) -> Path:
        return agent_session.SESSIONS_ROOT / self.run_label

    def discard_session(self, session_dir: Path | None) -> None:
        """Remove a finished run's session folder (question text, SQL, the worker's transcript summary). The run's
        steps and answer live on in the conversation store and its run-event chunks."""
        if session_dir is None:
            return
        session_dir = Path(session_dir)
        if session_dir.resolve().parent != self.sessions_root().resolve():
            raise ValueError("not a session folder of this driver")
        shutil.rmtree(session_dir, ignore_errors=True)

    def sweep_expired_sessions(self, retention_seconds: float = SESSION_RETENTION_SECONDS) -> int:
        """Remove session folders older than ``retention_seconds`` (left by a crash or a killed API process)."""
        root = self.sessions_root()
        if not root.is_dir():
            return 0
        cutoff = time.time() - retention_seconds
        removed = 0
        for session_dir in root.iterdir():
            try:
                expired = session_dir.is_dir() and session_dir.stat().st_mtime < cutoff
            except FileNotFoundError:
                continue
            if expired:
                shutil.rmtree(session_dir, ignore_errors=True)
                removed += 1
        return removed

    def worker_command(self, session_dir: Path) -> list[str]:
        effort = bundle(SHIPPED_ANALYZER_BUNDLE_ID).technique_config["effort"]
        return worker_command(str(session_dir), SHIPPED_ANALYZER_MODEL_ALIAS, effort)

    def answer(self, question_text: str, tap: LiveTap, cancelled: threading.Event, opener=None,
               prepared_sessions: list | None = None, on_snapshot_escalation=None) -> AnalyzerAnswer:
        """Run the question to its final answer, streaming steps into ``tap``. Raises ``Cancelled`` on Stop.

        ``opener``: the signed-in user's opener (real auth); the run then queries through it over the run channel,
        and it audits an escalation itself. ``on_snapshot_escalation(reason)``: without an opener the CLI escalates
        the snapshot session on its own, so the API audits it when the worker's ``escalate`` command succeeded.
        ``prepared_sessions``: the session folder is appended to it, so the caller can discard it once persisted."""
        session_dir = self.prepare_session(question_text, data_channel=opener is not None)
        if prepared_sessions is not None:
            prepared_sessions.append(session_dir)
        reader = RunReaderSession(opener) if opener is not None else None
        channel = None
        if reader is not None:
            from analyzer.cli import execute
            channel = RunChannel(session_dir, lambda argv: execute(argv, opener=reader))
        try:
            worker_result = self._run_worker(session_dir, tap, cancelled,
                                             None if reader is not None else on_snapshot_escalation)
            record_worker_usage(tap, worker_result)
            return final_answer_from_session(session_dir, worker_result, reader)
        finally:
            if channel is not None:
                channel.close()

    def _run_worker(self, session_dir: Path, tap: LiveTap, cancelled: threading.Event,
                    on_snapshot_escalation=None) -> dict:
        WORKER_DIRECTORY.mkdir(exist_ok=True)
        process = self.start_worker(self.worker_command(session_dir), WORKER_DIRECTORY,
                                    worker_environment(os.environ), session_dir / "claude_print.json",
                                    session_dir / "claude_print_stderr.txt")
        with self._workers_lock:
            self.live_workers.add(process)
        calls_tail = CallsFileTail(session_dir / "calls.jsonl")
        deadline = time.monotonic() + self.worker_timeout_seconds
        try:
            def observe_new_records():
                for call_record in calls_tail.new_records():
                    observe_call_record(tap, call_record)
                    if (on_snapshot_escalation is not None and call_record["command"] == "escalate"
                            and call_record["ok"]):
                        on_snapshot_escalation(str((call_record.get("argument") or {}).get("reason") or ""))

            while process.poll() is None:
                observe_new_records()
                if cancelled.is_set():
                    raise Cancelled()
                if time.monotonic() > deadline:
                    raise AnalyzerRunFailed(f"worker timed out after {self.worker_timeout_seconds} s")
                cancelled.wait(self.calls_poll_seconds)
            observe_new_records()
        finally:
            stop_worker_process(process)
            with self._workers_lock:
                self.live_workers.discard(process)
        return _read_worker_result(session_dir / "claude_print.json")


def _read_worker_result(result_path: Path) -> dict:
    try:
        return json.loads(result_path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {"is_error": True, "terminal_reason": "no JSON result"}


def final_answer_from_session(session_dir: Path, worker_result: dict, opener=None) -> AnalyzerAnswer:
    """``opener`` is the run's ``RunReaderSession`` (run channel): its own escalation state decides whether the answer
    is re-executed with lossless.* mounted. Without one the snapshot session's escalation.json does (the CLI wrote it
    after a granted ``escalate``)."""
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
    if opener is None and meta.get("data_channel"):
        raise AnalyzerRunFailed("a run-channel session's answer can only be re-executed through the run's opener")
    escalated = opener.escalated if opener is not None else load_escalated(session_dir)
    tools = ToolLayer(opener or SnapshotOpener(meta["snapshot_path"]), config_from_meta(meta, for_subagent=True),
                      restored_escalation=escalated)
    digest = tools.run_sql(final["sql"], charge=False, include_all_rows=True)
    return AnalyzerAnswer(kind="answer", text=final["text"], sql=final["sql"], rows=digest["all_rows"],
                          columns=digest["columns"], warnings=list(final.get("result_warnings") or []),
                          model=SHIPPED_ANALYZER_MODEL_ALIAS, usage=usage)
