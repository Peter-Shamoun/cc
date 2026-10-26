"""Contract 1.11.0: org-scoped read-only share links (CONTRACT.md §17)."""
import json

from api.tests.conftest import DX, LABS, events
from api.tests.test_titles import _validator

COLLEAGUE = {"Authorization": "Bearer demo:acme_labs:bo@xifin.example"}


def _answer(client):
    r = client.post("/api/chat", headers=LABS, json={"scenario": "chart", "messages": [
        {"id": "u1", "role": "user", "text": "Paid amount by service month"}]})
    evs = events(r)
    return evs[0]["conversationId"], evs[0]["messageId"], evs


def _share(client, cid, **body):
    return client.put(f"/api/conversations/{cid}/share", headers=LABS, json=body)


def test_share_defaults_to_no_data_and_colleague_sees_a_redacted_view(client):
    cid, mid, evs = _answer(client)
    client.put(f"/api/conversations/{cid}/messages/{mid}/feedback", headers=LABS,
               json={"rating": "down", "reasons": ["other"], "comment": "private note"})
    r = _share(client, cid)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    link = r.json()
    _validator("ShareLink").validate(link)
    assert link["includeData"] is False and link["scope"] == "organization" and link["url"] == f"/shared/{link['shareId']}"
    assert len(link["shareId"]) >= 24
    view = client.get(f"/api/shared/{link['shareId']}", headers=COLLEAGUE)
    assert view.status_code == 200 and view.headers["referrer-policy"] == "no-referrer"
    body = view.json()
    _validator("SharedConversation").validate(body)
    parts = [p for m in body["messages"] for p in m.get("parts", [])]
    table = next(p for p in parts if p["type"] == "table")
    assert table["rows"] == [] and table["dataWithheld"] is True and table["rowCount"] > 0 and "csvFileId" not in table
    assert not any(p["type"] == "chart" for p in parts)
    raw = json.dumps(body)
    assert "private note" not in raw and "feedback" not in raw and "ana@xifin.example" not in raw


def test_include_data_shares_preview_rows_but_never_the_csv(client):
    cid, _, evs = _answer(client)
    first = _share(client, cid).json()
    again = _share(client, cid, includeData=True).json()
    assert again["shareId"] == first["shareId"] and again["includeData"] is True  # one share per conversation
    assert client.get(f"/api/conversations/{cid}/share", headers=LABS).json() == again
    body = client.get(f"/api/shared/{again['shareId']}", headers=COLLEAGUE).json()
    table = next(p for m in body["messages"] for p in m.get("parts", []) if p["type"] == "table")
    assert table["rows"] and "dataWithheld" not in table and "csvFileId" not in table


def test_org_scope_revoke_and_delete(client):
    cid, _, _ = _answer(client)
    sid = _share(client, cid).json()["shareId"]
    other_org = client.get(f"/api/shared/{sid}", headers=DX)
    assert other_org.status_code == 404 and other_org.json()["error"]["code"] == "not_found"
    assert client.get(f"/api/shared/{sid}").status_code == 401
    # only the owner manages the share
    assert client.put(f"/api/conversations/{cid}/share", headers=COLLEAGUE, json={}).status_code == 404
    assert client.delete(f"/api/conversations/{cid}/share", headers=COLLEAGUE).status_code == 404
    assert client.delete(f"/api/conversations/{cid}/share", headers=LABS).json() == {"revoked": True}
    assert client.get(f"/api/shared/{sid}", headers=COLLEAGUE).status_code == 404
    assert client.delete(f"/api/conversations/{cid}/share", headers=LABS).status_code == 404
    assert client.get(f"/api/conversations/{cid}/share", headers=LABS).status_code == 404
    sid2 = _share(client, cid).json()["shareId"]
    assert sid2 != sid
    assert client.delete(f"/api/conversations/{cid}", headers=LABS).status_code == 200
    assert client.get(f"/api/shared/{sid2}", headers=COLLEAGUE).status_code == 404


def test_bad_body_and_unknown_ids(client):
    cid, _, _ = _answer(client)
    r = _share(client, cid, includeData="yes please")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    assert _share(client, "c_nope").status_code == 404
    assert client.get("/api/shared/sh_nope", headers=COLLEAGUE).status_code == 404
    assert client.get("/api/shared/bad%20id", headers=COLLEAGUE).status_code == 404


def test_audit_has_no_share_id(client, app):
    cid, _, _ = _answer(client)
    sid = _share(client, cid, includeData=True).json()["shareId"]
    client.get(f"/api/shared/{sid}", headers=COLLEAGUE)
    log = json.dumps(app.state.audit.events)
    assert "conversation_shared" in log and "shared_conversation_viewed" in log and sid not in log
