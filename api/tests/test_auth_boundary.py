"""Session/auth boundary for the later real backend: CSRF on cookie sessions, tokens never from URLs, no silent
demo fallback, AuthBackend shape. Synthetic principals only."""
import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.backends import AuthBackend, AuthContext, AuthError, DemoAuth, NoEscalateOpener
from api.store import Principal
from api.tests.conftest import LABS

TOKEN = LABS["Authorization"].split(" ", 1)[1]


def _client():
    return TestClient(create_app(settings=Settings(heartbeat_seconds=5)))


def test_cookie_session_mutation_needs_csrf_header():
    c = _client()
    c.cookies.set("cc_session", TOKEN)
    r = c.delete("/api/conversations")
    assert r.status_code == 403
    assert r.json() == {"error": {"code": "access_denied", "message": "Missing CSRF header."}}
    assert "default-src 'none'" in r.headers["content-security-policy"] and r.headers["cache-control"] == "no-store"
    assert c.delete("/api/conversations", headers={"x-cc-csrf": "1"}).status_code == 200


def test_cookie_session_reads_need_no_csrf_header():
    c = _client()
    c.cookies.set("cc_session", TOKEN)
    assert c.get("/api/conversations").status_code == 200


def test_bearer_requests_are_not_csrf_checked():
    c = _client()
    assert c.delete("/api/conversations", headers=LABS).status_code == 200


def test_csrf_check_covers_every_mutating_method():
    c = _client()
    c.cookies.set("cc_session", TOKEN)
    for method, path in [("POST", "/api/chat"), ("PUT", "/api/transcripts/retention"),
                         ("PATCH", "/api/conversations/c1"), ("DELETE", "/api/conversations/c1"),
                         ("POST", "/api/conversations/search")]:
        assert c.request(method, path, json={}).status_code == 403, (method, path)


def test_token_is_never_read_from_the_url():
    c = _client()
    for q in ("token", "access_token", "cc_session", "authorization"):
        assert c.get(f"/api/conversations?{q}={TOKEN}").status_code == 401


def test_non_demo_auth_mode_refuses_demo_fallback():
    with pytest.raises(RuntimeError, match="explicit auth backend"):
        create_app(settings=Settings(auth_mode="cognito"))
    create_app(settings=Settings(auth_mode="cognito"), auth=DemoAuth())  # explicit is fine


class FakeRealAuth:
    """What the real backend plugs in: any object with mode + authenticate()."""

    mode = "real"

    def authenticate(self, token: str, request_id: str) -> AuthContext:
        if token != "opaque-session-123":
            raise AuthError("invalid or expired session")
        inner = DemoAuth().authenticate(TOKEN, request_id).opener
        return AuthContext(Principal("acme_labs", "ana@xifin.example"), NoEscalateOpener(inner.inner))


def test_a_real_backend_plugs_in_through_the_protocol():
    auth: AuthBackend = FakeRealAuth()
    c = TestClient(create_app(auth=auth, settings=Settings(auth_mode="real", heartbeat_seconds=5)))
    c.cookies.set("cc_session", "opaque-session-123")
    assert c.get("/api/transcripts").status_code == 200
    assert c.put("/api/transcripts/retention", json={"days": 7}, headers={"x-cc-csrf": "1"}).json()["retentionDays"] == 7
    c.cookies.set("cc_session", "expired")
    r = c.get("/api/transcripts")
    assert r.status_code == 401 and r.json()["error"]["code"] == "auth_expired"
