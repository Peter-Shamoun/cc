"""HEAD support (RFC 9110 §9.3.2) and id-free httpx request logs."""
import logging

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from api.redact import outbound_shape

H = {"Authorization": "Bearer demo:acme_labs:qa@x.example"}


@pytest.fixture()
def client():
    return TestClient(create_app(), raise_server_exceptions=False)


@pytest.mark.parametrize("path", ["/api/health", "/api/capabilities", "/api/conversations", "/api/contract"])
def test_head_matches_get_without_body(client, path):
    g = client.get(path, headers=H)
    h = client.head(path, headers=H)
    assert h.status_code == g.status_code == 200
    assert h.content == b""
    assert h.headers.get("content-type") == g.headers.get("content-type")
    assert h.headers.get("content-length") == g.headers.get("content-length")
    for k in ("content-security-policy", "cache-control", "x-content-type-options", "strict-transport-security"):
        assert h.headers.get(k) == g.headers.get(k), k


def test_head_keeps_auth_and_error_codes(client):
    r = client.head("/api/conversations")
    assert r.status_code == 401 and r.content == b""
    assert client.head("/api/conversations/c_nope", headers=H).status_code == 404
    assert client.head("/api/shared/sh_nope", headers=H).status_code == 404


def test_head_on_sse_stream_is_still_405(client):
    assert client.head("/api/chat/m_x/stream", headers=H).status_code == 405


def test_head_does_not_unlock_post_only_routes(client):
    assert client.head("/api/chat", headers=H).status_code == 405


def test_outbound_shape():
    assert outbound_shape("http://testserver/api/shared/sh_AbC123?x=1") == "http://testserver/api/:id/:id"
    assert outbound_shape("https://idp.example/oauth2/token") .startswith("https://idp.example/")
    assert "sh_" not in outbound_shape("https://h/api/shared/sh_secret#frag")
    assert outbound_shape("not a url") == ":url"


def test_test_client_request_log_has_no_ids(client, caplog):
    caplog.set_level(logging.INFO, logger="httpx")
    client.get("/api/shared/sh_SyntheticShareId42", headers=H)
    client.get("/api/conversations/c_synthetic_7?q=Jane", headers=H)
    text = "\n".join(r.getMessage() for r in caplog.records if r.name == "httpx")
    assert "HTTP Request" in text
    assert "sh_SyntheticShareId42" not in text and "c_synthetic_7" not in text and "Jane" not in text
    assert "/api/:id/:id" in text  # 1.22: no share route, so the stale path is fully shaped
