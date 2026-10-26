"""Every API response carries the security headers: JSON, SSE, CSV, errors, validation failures, 404s."""
import pytest

from api.security import SECURITY_HEADERS
from api.tests.conftest import LABS, ask, parts


def assert_secure(response):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, (name, response.headers.get(name))
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp and "frame-ancestors 'none'" in csp
    assert "unsafe-inline" not in csp and "unsafe-eval" not in csp
    # one value per header, not a duplicated route value plus the middleware value
    assert response.headers.get_list("cache-control") == ["no-store"]


@pytest.mark.parametrize("method,path,headers,status", [
    ("get", "/api/health", {}, 200),
    ("get", "/api/conversations", LABS, 200),
    ("get", "/api/conversations", {}, 401),
    ("get", "/api/conversations/c_missing", LABS, 404),
    ("delete", "/api/conversations/c_missing", LABS, 404),
    ("get", "/api/download/f_missing", LABS, 404),
    ("post", "/api/chat/m_missing/stop", LABS, 404),
    ("get", "/no/such/route", {}, 404),
])
def test_headers_on_plain_responses(client, method, path, headers, status):
    response = getattr(client, method)(path, headers=headers)
    assert response.status_code == status
    assert_secure(response)


def test_headers_on_validation_error(client):
    response = client.post("/api/chat", json={"messages": "nope"}, headers=LABS)
    assert response.status_code == 422
    assert_secure(response)


def test_headers_on_sse_and_csv(client):
    response, evs = ask(client, "list all claims")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert_secure(response)
    file_id = next(p for p in parts(evs) if p["type"] == "file")["fileId"]
    csv = client.get(f"/api/download/{file_id}", headers=LABS)
    assert csv.status_code == 200
    assert_secure(csv)
    assert csv.headers["content-disposition"].startswith("attachment;")


def test_permissions_policy_matches_web():
    from pathlib import Path
    import re
    web = Path(__file__).resolve().parents[2] / "web" / "src" / "security" / "headers.ts"
    if not web.exists():
        pytest.skip("web/ not checked out")
    features = re.findall(r"'([a-z-]+=\([a-z]*\))'", web.read_text())
    assert ", ".join(features) == SECURITY_HEADERS["Permissions-Policy"]
