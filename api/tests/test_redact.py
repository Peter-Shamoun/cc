"""PHI-safe error paths: shared redaction vectors, the log filter, the 422 envelope and the 500 shield.
Synthetic data only."""
import json
import logging
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.redact import PhiLogFilter, redact, safe_exc
from api.tests.conftest import LABS

VECTORS = json.loads((Path(__file__).resolve().parents[2] / "web/src/lib/redact.vectors.json").read_text())["cases"]
PHI = ("Jane", "123-45-6789", "99887766", "jane.doe")


def test_shared_vectors():
    for case in VECTORS:
        assert redact(case["in"]) == case["out"], case["in"]


def test_redact_truncates():
    assert len(redact("x " * 1000)) <= 301


def test_safe_exc_has_no_message():
    try:
        int("Jane Doe 123-45-6789")
    except ValueError as exc:
        out = safe_exc(exc)
    assert out.startswith("ValueError at test_redact.py:") and "Jane" not in out


def test_log_filter_redacts_args_and_drops_exception_message():
    try:
        raise KeyError("Jane Doe MRN 99887766")
    except KeyError:
        import sys
        rec = logging.LogRecord("cc.api", logging.ERROR, __file__, 1, "lookup %s failed", ("jane.doe@example.org",),
                                sys.exc_info())
    assert PhiLogFilter().filter(rec)
    text = logging.Formatter().format(rec)
    assert "[email]" in text and "KeyError (message redacted)" in text
    assert not any(p in text for p in PHI)


def _client():
    return TestClient(create_app(settings=Settings(heartbeat_seconds=5)), raise_server_exceptions=False)


def test_422_is_contract_envelope_without_input_echo():
    body = {"messages": [{"role": "user", "text": "hi", "id": {"Jane Doe": "MRN 99887766"}}]}
    r = _client().post("/api/chat", json=body, headers=LABS)
    assert r.status_code == 422
    err = r.json()["error"]
    assert err["code"] == "validation" and err["message"].startswith("Invalid request")
    assert "messages.0.id" in err["message"]
    assert not any(p in r.text for p in PHI) and "input" not in r.json()
    assert r.headers["cache-control"] == "no-store"


def test_422_masks_user_controlled_keys():
    r = _client().post("/api/chat", json={"messages": {"Jane 123-45-6789": 1}}, headers=LABS)
    assert r.status_code == 422 and not any(p in r.text for p in PHI)


def test_unhandled_exception_is_500_envelope_and_log_has_no_message(caplog):
    app = create_app(settings=Settings(heartbeat_seconds=5))

    @app.get("/api/_boom")
    def boom():
        raise RuntimeError("Jane Doe SSN 123-45-6789")

    with caplog.at_level(logging.ERROR, logger="cc.api"):
        r = TestClient(app, raise_server_exceptions=False).get("/api/_boom")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal", "message": "Something went wrong. Try again in a moment."}}
    assert "default-src 'none'" in r.headers["content-security-policy"]  # shield sits inside SecurityHeaders
    logged = "\n".join(rec.getMessage() for rec in caplog.records)
    assert "RuntimeError" in logged and "/api/_boom" in logged
    assert not any(p in logged for p in PHI)


def test_log_filter_keeps_uvicorn_access_args_shape():
    """uvicorn's AccessFormatter unpacks five args; the filter must redact them without dropping them (QA C6)."""
    from uvicorn.logging import AccessFormatter
    rec = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, '%s - "%s %s HTTP/%s" %d',
                            ("127.0.0.1:5000", "GET", "/api/x?email=jane.doe@example.org", "1.1", 200), None)
    assert PhiLogFilter().filter(rec)
    text = AccessFormatter('%(client_addr)s "%(request_line)s" %(status_code)s', use_colors=False).format(rec)
    assert "[email]" in text and "200" in text and "jane.doe" not in text
