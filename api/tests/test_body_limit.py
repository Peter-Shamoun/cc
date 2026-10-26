"""Request-body cap (api/security.py BodyLimitMiddleware). Synthetic payloads only."""
import json

from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.tests.conftest import LABS, events


def _client(limit=4096):
    return TestClient(create_app(settings=Settings(heartbeat_seconds=5, max_body_bytes=limit)))


def _body(n):
    return json.dumps({"messages": [{"id": "u1", "role": "user", "text": "x" * n}]}).encode()


def test_oversized_body_is_413_with_security_headers():
    r = _client().post("/api/chat", content=_body(5000), headers={**LABS, "Content-Type": "application/json"})
    assert r.status_code == 413
    assert r.json() == {"error": {"code": "validation", "message": "Request body is too large."}}
    assert r.headers["cache-control"] == "no-store"
    assert "default-src 'none'" in r.headers["content-security-policy"]


def test_chunked_oversized_body_is_413():
    def gen():
        for _ in range(10):
            yield b"x" * 1000
    r = _client().post("/api/chat", content=gen(), headers={**LABS, "Content-Type": "application/json"})
    assert r.status_code == 413


def test_bodies_under_the_limit_still_work():
    client = _client()
    r = client.post("/api/chat", content=_body(100), headers={**LABS, "Content-Type": "application/json"})
    assert r.status_code == 200
    assert events(r)[-1]["type"] == "message.end"
    r = client.post("/api/conversations/search", json={"q": "claims"}, headers=LABS)
    assert r.status_code == 200


def test_default_limit_allows_a_long_chat_history():
    history = [{"id": f"u{i}", "role": "user", "text": "y" * 7000} for i in range(60)]  # ~420 KB
    history[-1]["text"] = "How many claims?"
    r = TestClient(create_app(settings=Settings(heartbeat_seconds=5))).post(
        "/api/chat", json={"messages": history}, headers=LABS)
    assert r.status_code == 200
