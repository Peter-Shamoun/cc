"""1.20.0 temporary chat (CONTRACT.md §11.1), like ChatGPT's Temporary chat: never saved (not in the store, not on
disk), never listed or searched, no title, can't be exported or rated; edit and regenerate still work inside
it; discard, delete-all, sign-out and the idle TTL drop it."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.contract import validate_event
from api.contract.conformance import check
from api.temporary import TemporaryChats
from api.tests.conftest import DX, LABS, events
from api.transcripts import DirBlobs, Keyring, SealedTranscriptStore

Q = "Paid amount trend by month"
SECRET = "Zanzibar-7731"  # a marker that must never reach the store, the audit log or disk


def _turn(client, text=Q, cid=None, headers=LABS, temporary=True, **extra):
    body = {"messages": [{"id": f"u{abs(hash((text, cid))) % 10**6}", "role": "user", "text": text}],
            "temporary": temporary, **extra}
    if cid:
        body["conversationId"] = cid
    r = client.post("/api/chat", json=body, headers=headers)
    return r, (events(r) if r.status_code == 200 else None)


@pytest.fixture
def app():
    return create_app(settings=Settings(heartbeat_seconds=5))


@pytest.fixture
def client(app):
    return TestClient(app)


def test_capabilities_advertise_temporary_chat(client):
    caps = client.get("/api/capabilities").json()
    assert caps["temporaryChat"]["ttlSeconds"] == 3600


def test_a_temporary_turn_streams_normally_but_is_never_saved_or_listed(app, client):
    r, evs = _turn(client, f"{Q} {SECRET}")
    assert check(evs) == [] and evs[-1]["finishReason"] == "stop"
    start = evs[0]
    validate_event(start)
    assert start["temporary"] is True
    assert "conversationTitle" not in evs[-1]  # no title, like ChatGPT
    cid = start["conversationId"]
    assert app.state.store.get_conversation(app.state.auth.authenticate(LABS["Authorization"].split(" ", 1)[1], "t")
                                            .principal, cid) is None
    assert client.get("/api/conversations", headers=LABS).json()["items"] == []
    assert client.post("/api/conversations/search", json={"query": "Zanzibar"}, headers=LABS).json()["items"] == []
    assert client.get(f"/api/conversations/{cid}", headers=LABS).status_code == 404
    assert client.get(f"/api/conversations/{cid}/export?format=markdown", headers=LABS).status_code == 404
    assert client.put(f"/api/conversations/{cid}/share", json={}, headers=LABS).status_code == 404
    mid = start["messageId"]
    assert client.put(f"/api/conversations/{cid}/messages/{mid}/feedback", json={"rating": "up"},
                      headers=LABS).status_code == 404
    assert client.patch(f"/api/conversations/{cid}", json={"title": "x"}, headers=LABS).status_code == 404
    assert SECRET not in str(app.state.audit.events)
    assert any(e["event"] == "chat_message" and e.get("temporary") is True for e in app.state.audit.events)


def test_a_normal_turn_has_no_temporary_flag(client):
    _, evs = _turn(client, temporary=False)
    assert "temporary" not in evs[0]


def test_follow_ups_edit_and_regenerate_work_inside_the_temporary_chat(client):
    _, evs = _turn(client)
    cid, m1 = evs[0]["conversationId"], evs[0]["messageId"]
    _, evs2 = _turn(client, "Paid amount by payer", cid, )
    assert evs2[0]["conversationId"] == cid and evs2[0]["temporary"] is True and check(evs2) == []
    r = client.post("/api/chat", json={"messages": [], "conversationId": cid, "temporary": True,
                                       "regenerate": {"messageId": m1}}, headers=LABS)
    evs3 = events(r)
    assert evs3[0]["regenerateOf"] == m1 and evs3[0]["temporary"] is True and check(evs3) == []


def test_a_chat_cannot_switch_between_temporary_and_saved(client):
    _, evs = _turn(client)
    tcid = evs[0]["conversationId"]
    r, _ = _turn(client, cid=tcid, temporary=False)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    _, evs = _turn(client, temporary=False)
    scid = evs[0]["conversationId"]
    r, _ = _turn(client, cid=scid, temporary=True)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    # a regenerate can't smuggle a temporary answer into a saved chat either
    r = client.post("/api/chat", json={"messages": [], "conversationId": scid, "temporary": True,
                                       "regenerate": {"messageId": evs[0]["messageId"]}}, headers=LABS)
    assert r.status_code == 422


def test_another_user_cannot_reach_a_temporary_chat(client):
    _, evs = _turn(client)
    cid = evs[0]["conversationId"]
    for temporary in (True, False):
        r, _ = _turn(client, cid=cid, headers=DX, temporary=temporary)
        assert r.status_code == 404
    assert client.delete(f"/api/conversations/{cid}", headers=DX).status_code == 404


def test_discard_delete_all_and_sign_out_end_temporary_chats(app, client):
    temp = app.state.temp_chats
    _, evs = _turn(client)
    cid = evs[0]["conversationId"]
    assert temp.has(cid)
    assert client.delete(f"/api/conversations/{cid}", headers=LABS).json() == {"deleted": True}
    assert not temp.has(cid)
    _, evs = _turn(client)
    assert client.delete("/api/conversations", headers=LABS).json() == {"deleted": 0}  # temporary ones aren't counted
    assert not temp.has(evs[0]["conversationId"])
    _, evs = _turn(client)
    client.post("/api/auth/sign-out", headers={**LABS, "Origin": "http://testserver"})
    assert not temp.has(evs[0]["conversationId"])


def test_idle_ttl_and_per_user_cap():
    from api.store import Principal
    t = TemporaryChats(ttl_seconds=10, max_per_user=2)
    p = Principal("acme_labs", "u")
    for i in range(3):
        t.append_message(p, f"c{i}", {"id": f"m{i}", "role": "user", "text": "q"})
    assert [t.has(f"c{i}") for i in range(3)] == [False, True, True]  # least recently used dropped
    assert t.purge_expired(time.time() + 11) == 2 and not t.has("c2")
    assert t.auto_title(p, "c1", "x") is False and t.set_feedback(p, "c1", "m1", {}) == "missing"
    with pytest.raises(RuntimeError):
        t.list_conversations(p)


def test_persisted_mode_writes_nothing_to_disk_for_a_temporary_chat(tmp_path):
    store = SealedTranscriptStore(DirBlobs(tmp_path / "blobs"), Keyring(tmp_path / "keys"))
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5), store=store))
    before = sorted(p.name for p in tmp_path.rglob("*"))
    _, evs = _turn(client, f"{Q} {SECRET}")
    assert evs[-1]["finishReason"] == "stop"
    assert sorted(p.name for p in tmp_path.rglob("*")) == before
    _turn(client, temporary=False)
    assert (tmp_path / "blobs").exists() and any((tmp_path / "blobs").rglob("*"))


@pytest.mark.parametrize("ttl, advertised", [(0, None), (-5, None), (0.4, 1), (90.6, 91)])
def test_ttl_setting_zero_turns_temporary_chat_off(ttl, advertised):
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5, temporary_ttl_seconds=ttl)))
    caps = client.get("/api/capabilities").json()
    assert caps.get("temporaryChat") == (None if advertised is None else {"ttlSeconds": advertised})
    r, _ = _turn(client)
    assert r.status_code == (422 if advertised is None else 200)
    _, evs = _turn(client, temporary=False)  # saved chats are unaffected
    assert evs[-1]["finishReason"] == "stop"
