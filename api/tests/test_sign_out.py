"""POST /api/auth/sign-out: server revoke via AuthBackend.sign_out, browser wipe via Clear-Site-Data, cookie expired."""
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.backends import AuthBackend, DemoAuth, SimAuth
from api.security import CSRF_HEADER, SESSION_COOKIE
from api.tests.conftest import LABS

TOKEN = "demo:acme_labs:ana@xifin.example"


class Recording(DemoAuth):
    def __init__(self, fail=False):
        super().__init__()
        self.revoked, self.fail = [], fail

    def sign_out(self, token):
        self.revoked.append(token)
        if self.fail:
            raise RuntimeError(f"backend down for {token}")


def _client(auth):
    return TestClient(create_app(auth=auth, settings=Settings(heartbeat_seconds=5)))


def _browser_cleared(r):
    assert r.status_code == 204 and r.content == b""
    assert r.headers["clear-site-data"] == '"cache", "storage"'
    assert r.headers["cache-control"] == "no-store"
    cookies = r.headers.get_list("set-cookie")
    assert {c.split("Path=")[1].split(";")[0] for c in cookies} == {"/api", "/"}, cookies  # spec'd Path=/api, plus /
    for cookie in cookies:
        assert cookie.startswith(f'{SESSION_COOKIE}="";') or cookie.startswith(f"{SESSION_COOKIE}=;"), cookie
        for attr in ("Max-Age=0", "HttpOnly", "Secure", "SameSite=strict"):
            assert attr.lower() in cookie.lower(), (attr, cookie)


def test_bearer_sign_out_revokes_and_clears():
    auth = Recording()
    c = _client(auth)
    _browser_cleared(c.post("/api/auth/sign-out", headers=LABS))
    assert auth.revoked == [LABS["Authorization"][7:]]
    assert c.app.state.audit.events[-1]["event"] == "signed_out"
    assert TOKEN not in str(c.app.state.audit.events[-1])


def test_cookie_sign_out_needs_csrf_header():
    auth = Recording()
    c = _client(auth)
    c.cookies.set(SESSION_COOKIE, TOKEN)
    r = c.post("/api/auth/sign-out")  # what a cross-site form post looks like
    assert r.status_code == 403 and "clear-site-data" not in r.headers and auth.revoked == []
    _browser_cleared(c.post("/api/auth/sign-out", headers={CSRF_HEADER: "1"}))
    assert auth.revoked == [TOKEN]


def test_signed_out_or_expired_callers_still_get_a_clean_browser():
    auth = Recording()
    c = _client(auth)
    _browser_cleared(c.post("/api/auth/sign-out"))  # no token at all: nothing to revoke
    assert auth.revoked == []
    _browser_cleared(c.post("/api/auth/sign-out", headers={"Authorization": "Bearer demo:nobody:x"}))
    assert auth.revoked == ["demo:nobody:x"]  # the backend decides; unknown tokens are a no-op there
    assert not any(e["event"] == "signed_out" for e in c.app.state.audit.events)


def test_a_failing_revoke_never_blocks_the_browser_wipe(caplog):
    c = _client(Recording(fail=True))
    _browser_cleared(c.post("/api/auth/sign-out", headers=LABS))
    assert "demo:acme_labs" not in caplog.text and "backend down" not in caplog.text


def test_backends_implement_sign_out():
    assert callable(getattr(AuthBackend, "sign_out"))
    assert DemoAuth().sign_out(TOKEN) is None
    assert SimAuth(aws=None, deployment=None).sign_out("x") is None


def test_legacy_backend_without_sign_out_still_clears():
    class Legacy:
        mode = "legacy"
        authenticate = DemoAuth().authenticate
    _browser_cleared(_client(Legacy()).post("/api/auth/sign-out", headers=LABS))
