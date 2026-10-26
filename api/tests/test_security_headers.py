"""Every api/ response carries the security headers (CONTRACT.md §10), including errors and SSE."""
import pytest

from api.security import API_CSP, SECURITY_HEADERS
from api.tests.conftest import LABS, ask, parts


def assert_secure(response):
    for name, value in SECURITY_HEADERS.items():
        assert response.headers.get(name) == value, (name, response.headers.get(name))
    assert "server" not in response.headers


def test_csp_denies_everything():
    assert "default-src 'none'" in API_CSP and "frame-ancestors 'none'" in API_CSP
    assert "unsafe" not in API_CSP


@pytest.mark.parametrize("path", ["/api/health", "/api/contract", "/api/conversations"])
def test_json_endpoints(client, path):
    response = client.get(path, headers=LABS)
    assert response.status_code == 200
    assert_secure(response)


def test_sse_stream_is_secure_and_unbuffered(client):
    response, evs = ask(client, "How many claims?")
    assert response.headers["content-type"].startswith("text/event-stream")
    assert response.headers["x-accel-buffering"] == "no"
    assert_secure(response)
    assert evs[-1]["type"] == "message.end"


@pytest.mark.parametrize("method,path,status", [
    ("get", "/api/nope", 404),
    ("get", "/api/conversations/c_missing", 404),
    ("post", "/api/chat/m_missing/stop", 404),
    ("get", "/api/download/d_missing", 404),
])
def test_errors_are_secure(client, method, path, status):
    response = getattr(client, method)(path, headers=LABS)
    assert response.status_code == status
    assert_secure(response)


def test_unauthorized_and_validation_errors_are_secure(client):
    assert_secure(client.get("/api/conversations"))
    bad = client.post("/api/chat", json={"messages": "nope"}, headers=LABS)
    assert bad.status_code in (400, 422)
    assert_secure(bad)


def test_csv_download_keeps_its_own_headers(client):
    _, evs = ask(client, "list all claims")
    file = next(p for p in parts(evs) if p["type"] == "file")
    response = client.get(f"/api/download/{file['fileId']}", headers=LABS)
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert_secure(response)


def test_no_openapi_or_docs(client):
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert client.get(path).status_code == 404
