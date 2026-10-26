"""Transcript store: ephemeral default, persisted (Design B) envelope encryption, chat-history endpoints."""
import json
import logging
import os
import stat

import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.store import InMemoryTranscriptStore, Principal
from api.tests.conftest import DX, LABS, ask
from api.transcripts import (CustomerKeyDisabled, DirBlobs, Keyring, MemoryBlobs, SealedTranscriptStore,
                             make_store, resolve_mode)

A = Principal("acme_labs", "ana@xifin.example")
A2 = Principal("acme_labs", "bo@xifin.example")
B = Principal("acme_diagnostics", "dev@xifin.example")
SECRET = "Patient Zelda Quixote MRN 99887766"


def sealed(tmp_path=None):
    blobs = DirBlobs(tmp_path / "blobs") if tmp_path else MemoryBlobs()
    return SealedTranscriptStore(blobs, Keyring(tmp_path / "keys" if tmp_path else None))


@pytest.fixture(params=["ephemeral", "persisted"])
def store(request):
    return InMemoryTranscriptStore() if request.param == "ephemeral" else sealed()


def test_default_mode_is_ephemeral(monkeypatch):
    monkeypatch.delenv("CC_TRANSCRIPT_MODE", raising=False)
    assert resolve_mode() == "ephemeral"
    assert isinstance(make_store(), InMemoryTranscriptStore)
    monkeypatch.setenv("CC_TRANSCRIPT_MODE", "persisted")
    assert isinstance(make_store(), SealedTranscriptStore)
    monkeypatch.setenv("CC_TRANSCRIPT_MODE", "sideways")
    with pytest.raises(ValueError):
        make_store()


def test_store_contract(store):
    store.append_message(A, "c1", {"id": "m1", "role": "user", "text": SECRET})
    store.append_message(A, "c1", {"id": "m2", "role": "assistant", "parts": [{"type": "text", "id": "p", "text": "Denials rose 4%."}]})
    store.append_message(A, "c2", {"id": "m3", "role": "user", "text": "paid by payer"})
    assert store.get_conversation(B, "c1") is None and store.get_conversation(A2, "c1") is None
    assert store.owned_by_other(B, "c1") and not store.owned_by_other(A, "c1") and not store.owned_by_other(B, "c_new")
    items = store.list_conversations(A)["items"]
    assert [i["id"] for i in items] == ["c2", "c1"]
    assert items[1] == {"id": "c1", "title": "New analysis", "updatedAt": items[1]["updatedAt"], "messageCount": 2,
                        "pinned": False, "archived": False}  # 1.17.0 flags
    assert store.list_conversations(B)["items"] == []
    # search: title and message text, owner only, snippet around the hit
    hits = store.list_conversations(A, query="zelda")["items"]
    assert [h["id"] for h in hits] == ["c1"] and "Zelda" in hits[0]["snippet"]
    assert store.list_conversations(A, query="rose 4")["items"][0]["id"] == "c1"
    assert store.list_conversations(B, query="zelda")["items"] == []
    # rename
    assert store.rename_conversation(A, "c1", "  Denials\n Q3  ")["title"] == "Denials Q3"
    assert store.rename_conversation(B, "c1", "x") is None
    assert store.get_conversation(A, "c1")["title"] == "Denials Q3"
    assert store.list_conversations(A, query="q3")["items"][0]["snippet"] == "Denials Q3"
    assert store.rename_conversation(A, "c1", "")["title"] == "New analysis"
    with pytest.raises(ValueError):
        store.rename_conversation(A, "c1", "a\x00b")
    # delete one, delete all
    assert not store.delete_conversation(B, "c1")
    assert store.delete_conversation(A, "c2") and not store.delete_conversation(A, "c2")
    store.append_message(B, "c9", {"id": "m9", "role": "user", "text": "q"})
    assert store.delete_all(A) == 1 and store.list_conversations(A)["items"] == []
    assert store.list_conversations(B)["items"][0]["id"] == "c9"
    with pytest.raises(PermissionError):
        store.append_message(A, "c9", {"id": "x", "role": "user", "text": "hijack"})


def test_retention(store):
    store.append_message(A, "c1", {"id": "m1", "role": "user", "text": "q"})
    store.set_retention(A, 0)
    assert store.list_conversations(A)["items"] == []
    with pytest.raises(ValueError):
        store.set_retention(A, -1)


def test_sealed_store_writes_no_plaintext(tmp_path):
    s = sealed(tmp_path)
    s.append_message(A, "c1", {"id": "m1", "role": "user", "text": SECRET})
    s.rename_conversation(A, "c1", "Zelda follow-up")
    files = [p for p in (tmp_path / "blobs").rglob("*") if p.is_file()]
    assert {p.name for p in files} == {"c1.json", "c1.bin", "dek.bin", ".lock"}  # .lock: empty, for conditional writes
    for p in files:
        raw = p.read_bytes()
        assert b"Zelda" not in raw and b"99887766" not in raw and b"ana@" not in raw and b"follow-up" not in raw
        assert stat.S_IMODE(p.stat().st_mode) == 0o600
    assert all("ana" not in str(p) for p in files)  # user id hashed in paths
    meta = json.loads(next(p for p in files if p.name == "c1.json").read_text())
    assert set(meta) == {"id", "customer", "createdAt", "updatedAt", "messageCount"}
    # survives a restart with the same key directory
    again = SealedTranscriptStore(DirBlobs(tmp_path / "blobs"), Keyring(tmp_path / "keys"))
    assert again.get_conversation(A, "c1")["messages"][0]["text"] == SECRET


def test_sealed_blobs_are_bound_to_owner_and_chat(tmp_path):
    s = sealed(tmp_path)
    s.append_message(A, "c1", {"id": "m1", "role": "user", "text": SECRET})
    s.append_message(A, "c2", {"id": "m2", "role": "user", "text": "other"})
    root = tmp_path / "blobs"
    c1 = next(root.rglob("c1.bin"))
    c2 = next(root.rglob("c2.bin"))
    c2.write_bytes(c1.read_bytes())  # swap content between chats: AES-GCM AAD must reject it
    with pytest.raises(Exception):
        s.get_conversation(A, "c2")


def test_delete_all_destroys_data_key_and_shred_kills_customer(tmp_path):
    s = sealed(tmp_path)
    s.append_message(A, "c1", {"id": "m1", "role": "user", "text": SECRET})
    s.append_message(B, "c2", {"id": "m2", "role": "user", "text": "q"})
    assert s.delete_all(A) == 1
    assert not list((tmp_path / "blobs" / "acme_labs").rglob("dek.bin"))
    s.crypto_shred_customer("acme_diagnostics")
    with pytest.raises(CustomerKeyDisabled):
        s.list_conversations(B)
    again = SealedTranscriptStore(DirBlobs(tmp_path / "blobs"), Keyring(tmp_path / "keys"))
    with pytest.raises(CustomerKeyDisabled):
        again.get_conversation(B, "c2")


def test_dir_blobs_reject_traversal(tmp_path):
    with pytest.raises(ValueError):
        DirBlobs(tmp_path).put("../escape", b"x")


@pytest.fixture(params=["ephemeral", "persisted"])
def api(request):
    app = create_app(settings=Settings(heartbeat_seconds=5), store=make_store(request.param))
    return app, TestClient(app)


def test_history_endpoints(api, caplog):
    app, client = api
    caplog.set_level(logging.DEBUG)
    _, evs = ask(client, "denial rate by payer")
    cid = evs[0]["conversationId"]
    info = client.get("/api/transcripts", headers=LABS).json()
    assert info["mode"] == app.state.store.mode
    listed = client.get("/api/conversations", headers=LABS).json()["items"]
    assert listed[0]["id"] == cid and listed[0]["messageCount"] == 2
    r = client.patch(f"/api/conversations/{cid}", json={"title": "Payer denials"}, headers=LABS)
    assert r.status_code == 200 and r.json()["title"] == "Payer denials"
    assert client.patch(f"/api/conversations/{cid}", json={"title": "x"}, headers=DX).status_code == 404
    assert client.patch(f"/api/conversations/{cid}", json={"title": "a\u0007b"}, headers=LABS).status_code == 422
    assert client.get(f"/api/conversations/{cid}", headers=LABS).json()["title"] == "Payer denials"
    hits = client.post("/api/conversations/search", json={"q": "denial rate"}, headers=LABS).json()["items"]
    assert hits and hits[0]["id"] == cid and "snippet" in hits[0]
    assert client.post("/api/conversations/search", json={"q": "denial rate"}, headers=DX).json()["items"] == []
    assert client.post("/api/conversations/search", json={"q": "  "}, headers=LABS).json() == {"items": []}
    assert client.post("/api/conversations/search", json={"q": "x"}).status_code == 401
    # never in logs: the search text, the title, the question
    assert "Payer denials" not in caplog.text and "denial rate" not in caplog.text
    events = [e["event"] for e in app.state.audit.events]
    assert "conversation_renamed" in events
    assert client.put("/api/transcripts/retention", json={"days": 30}, headers=LABS).json()["retentionDays"] == 30
    assert client.put("/api/transcripts/retention", json={"days": -1}, headers=LABS).status_code == 422
    assert client.delete("/api/conversations", headers=DX).json() == {"deleted": 0}
    assert client.delete("/api/conversations", headers=LABS).json() == {"deleted": 1}
    assert client.get("/api/conversations", headers=LABS).json()["items"] == []


def test_chat_rejects_bad_or_foreign_conversation_ids(api):
    _, client = api
    _, evs = ask(client, "How many claims?")
    cid = evs[0]["conversationId"]
    body = {"conversationId": cid, "messages": [{"role": "user", "text": "hi"}]}
    assert client.post("/api/chat", json=body, headers=DX).status_code == 404
    bad = {"conversationId": "../../etc", "messages": [{"role": "user", "text": "hi"}]}
    assert client.post("/api/chat", json=bad, headers=LABS).status_code in (404, 422)


def test_shredded_customer_gets_410_not_500():
    store = sealed()
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5), store=store))
    ask(client, "How many claims?")
    store.crypto_shred_customer("acme_labs")
    assert client.get("/api/conversations", headers=LABS).status_code == 410
    r = client.post("/api/chat", json={"messages": [{"role": "user", "text": "hi"}]}, headers=LABS)
    assert r.status_code == 410 and r.json()["error"]["code"] == "transcripts_unavailable"
    assert client.get("/api/conversations", headers=DX).json() == {"items": []}
