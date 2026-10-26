"""Contract 1.22.0: sharing is removed. Every former share route and any /api/share* path answers with the
standard 404 envelope for every method, signed in or not, and nothing about sharing is left in the contract."""
import json

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.contract import CONTRACT_VERSION, schema as contract_schema
from api.tests.conftest import LABS, events

ENVELOPE = {"error": {"code": "not_found", "message": "Not found."}}
COLLEAGUE = {"Authorization": "Bearer demo:acme_labs:bo@xifin.example"}


@pytest.fixture(scope="module")
def client():
    return TestClient(create_app(settings=Settings(heartbeat_seconds=5)))


@pytest.fixture(scope="module")
def cid(client):
    r = client.post("/api/chat", headers=LABS, json={"messages": [{"id": "u1", "role": "user", "text": "list all claims"}]})
    return events(r)[0]["conversationId"]


def _paths(cid):
    return [f"/api/conversations/{cid}/share", "/api/conversations/c_missing00000000/share",
            "/api/shared/sh_AbC-d_9xYz12345678901234", "/api/shared/", "/api/shared",
            "/api/share", "/api/share/", "/api/share/sh_x", "/api/share/links", "/api/share/sh_x/rotate",
            "/api/shares", f"/api/conversations/{cid}/messages/m_1/share"]


@pytest.mark.parametrize("method", ["GET", "PUT", "POST", "DELETE", "PATCH"])
@pytest.mark.parametrize("headers", [LABS, COLLEAGUE, {}], ids=["owner", "colleague", "signed_out"])
def test_every_share_path_is_the_standard_404(client, cid, method, headers):
    for path in _paths(cid):
        r = client.request(method, path, headers=headers, json={"includeData": True, "expiresInDays": 7})
        assert r.status_code == 404, (method, path, r.status_code)
        assert r.json() == ENVELOPE, (method, path)
        assert "sh_" not in r.text and "share" not in r.text.lower()


def test_head_is_404_too(client, cid):
    assert client.head(f"/api/conversations/{cid}/share", headers=LABS).status_code == 404
    assert client.head("/api/shared/sh_x", headers=COLLEAGUE).status_code == 404


def test_conversation_is_untouched_by_a_share_attempt(client, cid):
    client.put(f"/api/conversations/{cid}/share", headers=LABS, json={"includeData": True})
    assert client.get(f"/api/conversations/{cid}", headers=LABS).status_code == 200
    assert client.get(f"/api/conversations/{cid}", headers=COLLEAGUE).status_code == 404  # still owner-only


def test_no_share_route_is_registered(client):
    paths = [r.path for r in client.app.routes if isinstance(r, APIRoute)]
    assert not [p for p in paths if "share" in p], paths
    assert not hasattr(client.app.state, "shares")


def test_capabilities_have_no_share_flag(client):
    for h in (LABS, {}):
        caps = client.get("/api/capabilities", headers=h).json()
        assert "shareExpiry" not in caps and caps["contractVersion"] == CONTRACT_VERSION


def test_schema_has_no_share_shapes():
    schema = contract_schema()
    defs = schema["$defs"]
    assert not [k for k in defs if k.lower().startswith("share")]
    assert "shareExpiry" not in defs["Capabilities"]["properties"]
    assert "dataWithheld" not in defs["TablePart"]["properties"]
    text = json.dumps(schema).replace("null_share", "")
    assert "share" not in text.lower(), "sharing is gone; only the step check id `null_share` may mention it"


def test_share_module_is_gone():
    with pytest.raises(ModuleNotFoundError):
        __import__("api.share")
