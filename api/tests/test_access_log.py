"""Access logs never carry share ids, record ids, query strings or full client IPs (shell saw /api/shared/<id>)."""
import logging
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from api.app import create_app
from api.redact import ACCESS_FILTER, mask_client, path_shape
from api.tests.timing import budget, load_factor

ROOT = Path(__file__).resolve().parents[2]
STATIC = frozenset({"api", "shared", "conversations", "messages", "feedback", "download", "chat", "stream", "share"})


@pytest.mark.parametrize("path,want", [
    ("/api/shared/sh_AbC-d_9xYz12345678901234", "/api/shared/:id"),
    ("/api/conversations/c_x1y2z3w4v5u6", "/api/conversations/:id"),
    ("/api/conversations/c_1/messages/m_2/feedback", "/api/conversations/:id/messages/:id/feedback"),
    ("/api/download/f_abc?name=Jane%20Doe", "/api/download/:id?\u2026"),
    ("/api/chat/m_1/stream?last=3", "/api/chat/:id/stream?\u2026"),
    ("/Jane-Doe-123-45-6789", "/:id"),
    ("/api/health", "/api/:id"),  # not in this test's STATIC
])
def test_path_shape(path, want):
    assert path_shape(path, STATIC) == want


@pytest.mark.parametrize("client,want", [
    ("203.0.113.57:51234", "203.0.113.0"), ("10.1.2.3", "10.1.2.0"),
    ("[2001:db8:85a3:8d3:1319:8a2e:370:7348]:443", "2001:db8:85a3::"), ("unix-socket", "-"), (None, "-"),
])
def test_mask_client(client, want):
    assert mask_client(client) == want


def test_create_app_registers_every_static_route_segment():
    app = create_app()
    for r in app.routes:
        for seg in str(getattr(r, "path", "")).split("/"):
            if seg and "{" not in seg:
                assert seg in ACCESS_FILTER.static
    assert ACCESS_FILTER in logging.getLogger("uvicorn.access").filters


def test_filter_rewrites_uvicorn_shaped_record():
    create_app()
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("198.51.100.7:4000", "GET", "/api/shared/sh_secretToken-_42?x=Jane", "1.1", 200), None)
    for f in logging.getLogger("uvicorn.access").filters:
        f.filter(rec)
    assert rec.getMessage() == '198.51.100.0 - "GET /api/shared/:id?\u2026 HTTP/1.1" 200'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_real_uvicorn_access_log_is_redacted():
    """The plain ``uvicorn api.app:app`` command (access log ON by default), as Playwright and the docs run it."""
    port = _free_port()
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "api.app:app", "--no-server-header", "--host", "127.0.0.1",
                             "--port", str(port)], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    secrets_in_paths = ["sh_ZZsecretTOKEN-_9876543210", "c_conv12345678", "Jane", "123-45-6789"]
    try:
        for _ in range(int(100 * load_factor())):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=budget(1))
                break
            except OSError:
                time.sleep(0.1)
        for path in ["/api/shared/sh_ZZsecretTOKEN-_9876543210", "/api/conversations/c_conv12345678?q=Jane",
                     "/api/download/123-45-6789"]:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=budget(2))
            except OSError:
                pass  # 401/404 are fine; we only care what got logged
        time.sleep(0.3)
    finally:
        proc.terminate()
        out, _ = proc.communicate(timeout=budget(10))
    access = [line for line in out.splitlines() if '"GET ' in line]
    assert len(access) >= 4, out
    assert any("/api/shared/:id" in line for line in access), access
    assert any("/api/conversations/:id?\u2026" in line for line in access), access
    for s in secrets_in_paths + ["127.0.0.1"]:
        assert s not in "\n".join(access), (s, access)
