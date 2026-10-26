"""A run's command channel: how ``python3 -m analyzer.cli`` reaches the signed-in user's data from a ``claude -p`` worker.

The chat API (``api.claude_code_cli_driver``) opens the run's reader session itself (``analyzer.opener.QueryPathOpener``
→ ``ccsim.query.open_session``: Cognito token → STS → manifest → per-relation S3 secrets, ``sem.*`` only) and serves
this run's analyzer commands on a unix socket while the worker lives. The CLI process the model starts sends its argv
and gets back the exit code and the printed text, nothing else. So:

- the temporary AWS credentials and the DuckDB connection never leave the API process: the worker's processes see
  query results, never a credential, and nothing credential-bearing is written to disk;
- the customer is fixed when the run starts, from the verified token; a command carries no customer, token or
  connection setting, and a ``--session`` other than the run's own is refused;
- the channel exists only for one run: a socket in a fresh 0700 directory, a 0600 socket file, and a random
  capability in the session directory (``run_channel.json``, 0600) that every request must present. Both are
  removed when the run ends, so a later command (or another run's worker) reaches nothing.

Framing: the client writes one JSON object and shuts down its write side; the server answers with one JSON object.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import socket
import tempfile
import threading
from pathlib import Path
from typing import Callable

CHANNEL_FILE = "run_channel.json"
DATA_CHANNEL_KIND = "run_socket"
MAXIMUM_REQUEST_BYTES = 1 << 20
CLIENT_TIMEOUT_SECONDS = 900


class RunChannelError(RuntimeError):
    pass


def _receive_all(connection: socket.socket, limit: int) -> bytes:
    chunks, total = [], 0
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > limit:
            raise RunChannelError("request too large")
        chunks.append(chunk)


def send_command(session_dir: Path, argv: list[str]) -> tuple[int, str]:
    """Client side (the CLI the worker runs): one command over the run's channel."""
    try:
        channel = json.loads((session_dir / CHANNEL_FILE).read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        raise RunChannelError("this run has ended: its data channel is closed") from None
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(CLIENT_TIMEOUT_SECONDS)
    try:
        client.connect(channel["socket"])
        client.sendall(json.dumps({"capability": channel["capability"], "argv": argv}).encode())
        client.shutdown(socket.SHUT_WR)
        reply = json.loads(_receive_all(client, 64 << 20) or b"{}")
    except (OSError, json.JSONDecodeError):
        raise RunChannelError("this run's data channel is unreachable") from None
    finally:
        client.close()
    if "exit_code" not in reply:
        raise RunChannelError(reply.get("error") or "the data channel refused the command")
    return int(reply["exit_code"]), str(reply.get("output", ""))


def _session_argument(argv: list[str]) -> str | None:
    for index, value in enumerate(argv):
        if value == "--session" and index + 1 < len(argv):
            return argv[index + 1]
        if value.startswith("--session="):
            return value.split("=", 1)[1]
    return None


class RunChannel:
    """Server side (the API process): serves one run's commands with ``run_command(argv) -> (exit code, output)``.

    Commands run one at a time (the session's files and the reader connection are not shared between commands)."""

    def __init__(self, session_dir: Path, run_command: Callable[[list[str]], tuple[int, str]]):
        self.session_dir = Path(session_dir).resolve()
        self.run_command = run_command
        self.capability = secrets.token_urlsafe(32)
        self.socket_dir = Path(tempfile.mkdtemp(prefix="cc-run-"))     # mkdtemp: 0700
        self.socket_path = self.socket_dir / "s"
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        self.server.listen(8)
        self.commands_served = 0
        self.requests_refused = 0
        self._closed = threading.Event()
        self._command_lock = threading.Lock()
        descriptor = os.open(self.session_dir / CHANNEL_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "w") as channel_file:
            json.dump({"socket": str(self.socket_path), "capability": self.capability}, channel_file)
        self._thread = threading.Thread(target=self._serve, name="run-channel", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while not self._closed.is_set():
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(connection,), name="run-channel-command", daemon=True).start()

    def _reply(self, connection: socket.socket, body: dict) -> None:
        try:
            connection.sendall(json.dumps(body).encode())
        except OSError:
            pass

    def _handle(self, connection: socket.socket) -> None:
        with connection:
            try:
                request = json.loads(_receive_all(connection, MAXIMUM_REQUEST_BYTES))
            except (OSError, RunChannelError, json.JSONDecodeError):
                self.requests_refused += 1
                return self._reply(connection, {"error": "malformed request"})
            refusal = self._refusal(request)
            if refusal:
                self.requests_refused += 1
                return self._reply(connection, {"error": refusal})
            with self._command_lock:
                if self._closed.is_set():
                    return self._reply(connection, {"error": "this run has ended"})
                try:
                    exit_code, output = self.run_command(list(request["argv"]))
                except SystemExit:                     # argparse usage error: its message went to stderr
                    exit_code, output = 2, "ERROR: invalid command arguments"
                self.commands_served += 1
            self._reply(connection, {"exit_code": exit_code, "output": output})

    def _refusal(self, request) -> str | None:
        if not isinstance(request, dict) or not isinstance(request.get("argv"), list) or not all(
                isinstance(item, str) for item in request["argv"]):
            return "malformed request"
        if not hmac.compare_digest(str(request.get("capability", "")).encode(), self.capability.encode()):
            return "not this run's channel"
        session = _session_argument(request["argv"])
        if session is None or Path(session).resolve() != self.session_dir:
            return "a command may only name this run's session"
        return None

    def close(self) -> None:
        """Stop serving: the capability file and the socket go first, so no new command can start."""
        self._closed.set()
        (self.session_dir / CHANNEL_FILE).unlink(missing_ok=True)
        try:
            self.server.shutdown(socket.SHUT_RDWR)     # wakes the accept() in _serve
        except OSError:
            pass
        try:
            self.server.close()
        finally:
            shutil.rmtree(self.socket_dir, ignore_errors=True)
        with self._command_lock:                       # wait for a command already running to return
            pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False
