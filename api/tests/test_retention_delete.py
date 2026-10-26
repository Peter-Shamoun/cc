"""Retention sweep and deletion that honours the transcript encryption design (docs/frontend_transcripts.md).
Synthetic data only."""
import json

from fastapi.testclient import TestClient

from api.app import Settings, _Run, create_app
from api.store import DownloadRegistry, InMemoryTranscriptStore, Principal
from api.tests.conftest import LABS, ask
from api.tests.timing import wait_until
from api.transcripts import Keyring, MemoryBlobs, SealedTranscriptStore

A = Principal("acme_labs", "ana@xifin.example")
B = Principal("acme_labs", "bo@xifin.example")
MSG = {"id": "u1", "role": "user", "text": "Patient Zelda Quixote MRN 99887766"}
DAY = 86400


def _age(store, principal, cid, days):
    """Backdate a conversation's updatedAt (both store kinds)."""
    if isinstance(store, InMemoryTranscriptStore):
        store._items[cid]["updatedAt"] -= days * DAY
        return
    key = f"{store._prefix(principal)}meta/{cid}.json"
    meta = json.loads(store.blobs.get(key))
    meta["updatedAt"] -= days * DAY
    store.blobs.put(key, json.dumps(meta).encode())


def test_sealed_retention_survives_restart_and_sweep_reaches_absent_users():
    blobs, keys = MemoryBlobs(), Keyring()
    s1 = SealedTranscriptStore(blobs, keys)
    s1.append_message(A, "c1", MSG)
    s1.append_message(B, "c2", MSG)
    s1.set_retention(A, 7)
    s2 = SealedTranscriptStore(blobs, keys)  # a restart: in-memory state gone
    assert s2.retention_days(A) == 7 and s2.retention_days(B) == 90
    _age(s2, A, "c1", 8)
    _age(s2, B, "c2", 8)  # B keeps the 90-day default
    audits = []
    s2._audit = lambda event, **kw: audits.append((event, kw))
    assert s2.purge_expired() == 1  # without A or B ever calling list/get
    prefix_a = s2._prefix(A)
    assert not [k for k in blobs.list(prefix_a) if "/meta/" in k or "/content/" in k]
    assert blobs.get(f"{prefix_a}dek.bin") is None  # nothing left: the user's data key is shredded too
    assert blobs.get(f"{prefix_a}retention.json") is not None  # the setting (days only) stays
    assert s2.get_conversation(B, "c2") is not None
    assert audits == [("transcripts_expired", {"row_count": 1})]
    s2.append_message(A, "c3", MSG)  # a new key is made on the next write
    assert s2.get_conversation(A, "c3")["messages"][0]["text"] == MSG["text"]


def test_sealed_sweep_keeps_key_while_live_conversations_remain():
    s = SealedTranscriptStore(MemoryBlobs(), Keyring())
    s.append_message(A, "old", MSG)
    s.append_message(A, "new", MSG)
    _age(s, A, "old", 91)
    assert s.purge_expired() == 1
    assert s.get_conversation(A, "new") is not None
    assert s.get_conversation(A, "old") is None


def test_sealed_delete_all_keeps_retention_setting_only():
    s = SealedTranscriptStore(MemoryBlobs(), Keyring())
    s.append_message(A, "c1", MSG)
    s.set_retention(A, 30)
    assert s.delete_all(A) == 1
    assert s.blobs.list(s._prefix(A)) == [f"{s._prefix(A)}retention.json"]
    assert json.loads(s.blobs.get(f"{s._prefix(A)}retention.json")) == {"days": 30}


def test_sealed_stored_retention_is_capped_and_tolerates_garbage():
    s = SealedTranscriptStore(MemoryBlobs(), Keyring())
    s.blobs.put(f"{s._prefix(A)}retention.json", b'{"days": 9999}')
    s.blobs.put(f"{s._prefix(B)}retention.json", b"not json")
    assert s._stored_days(s._prefix(A)) == 90
    fresh = SealedTranscriptStore(s.blobs, s.keyring)
    assert fresh.retention_days(A) == 90 and fresh.retention_days(B) == 90


def test_memory_sweep_reaches_absent_users():
    s = InMemoryTranscriptStore()
    s.append_message(A, "c1", MSG)
    s.append_message(B, "c2", MSG)
    s.set_retention(A, 1)
    _age(s, A, "c1", 2)
    _age(s, B, "c2", 2)  # B never set a retention: ephemeral keeps until restart
    assert s.purge_expired() == 1
    assert s.get_conversation(A, "c1") is None and s.get_conversation(B, "c2") is not None


def test_download_registry_scoping_and_revoke():
    reg = DownloadRegistry()
    d1 = reg.scoped("c1").put(A, "a.csv", ["x"], [[1]])
    d2 = reg.scoped("c2").put(A, "b.csv", ["x"], [[1]])
    d3 = reg.scoped("c1").put(B, "c.csv", ["x"], [[1]])
    assert reg.revoke(A, "c1") == 1
    assert reg.get(A, d1.id) is None and reg.get(A, d2.id) is not None and reg.get(B, d3.id) is not None
    assert reg.revoke(A) == 1 and reg.get(A, d2.id) is None


def _conversation_id(client):
    r = client.get("/api/conversations", headers=LABS).json()
    return r["items"][0]["id"]


def test_delete_revokes_downloads_and_clarifications_and_discards_runs():
    app = create_app(settings=Settings(heartbeat_seconds=5, transcript_sweep_seconds=0))
    client = TestClient(app)
    ask(client, "How many claims?")
    cid = _conversation_id(client)
    principal = app.state.auth.authenticate(LABS["Authorization"].split(" ", 1)[1], "t").principal
    dl = app.state.downloads.scoped(cid).put(principal, "r.csv", ["x"], [[1]])
    keep = app.state.downloads.scoped("other").put(principal, "k.csv", ["x"], [[1]])
    app.state.clarifications["k1"] = {"principal": principal, "conversation_id": cid, "question": "q"}
    live = _Run("m_live", principal, conversation_id=cid)
    app.state.runs["m_live"] = live
    assert client.delete(f"/api/conversations/{cid}", headers=LABS).json() == {"deleted": True}
    assert live.discarded.is_set() and live.cancelled.is_set()
    assert "k1" not in app.state.clarifications
    assert client.get(f"/api/download/{dl.id}", headers=LABS).status_code == 404
    assert app.state.downloads.get(principal, keep.id) is not None
    assert client.get(f"/api/conversations/{cid}", headers=LABS).status_code == 404


def test_delete_mid_stream_does_not_resurrect_the_conversation():
    app = create_app(settings=Settings(heartbeat_seconds=5, stream_delay_seconds=0.02, transcript_sweep_seconds=0))
    client = TestClient(app)
    body = {"messages": [{"id": "u1", "role": "user", "text": "How many claims were denied last month?"}]}
    with client.stream("POST", "/api/chat", json=body, headers=LABS) as r:
        lines = r.iter_lines()
        for line in lines:
            if line.startswith("data:") and "text.delta" in line:
                break
        cid = _conversation_id(client)
        assert client.delete(f"/api/conversations/{cid}", headers=LABS).json() == {"deleted": True}
        for _ in lines:
            pass
    wait_until(lambda: all(run.done for run in app.state.runs.values()), 5)
    assert client.get(f"/api/conversations/{cid}", headers=LABS).status_code == 404
    assert client.get("/api/conversations", headers=LABS).json()["items"] == []


def test_delete_all_forgets_every_run():
    app = create_app(settings=Settings(heartbeat_seconds=5, transcript_sweep_seconds=0))
    client = TestClient(app)
    principal = app.state.auth.authenticate(LABS["Authorization"].split(" ", 1)[1], "t").principal
    runs = [_Run(f"m_{i}", principal, conversation_id=f"c{i}") for i in range(2)]
    other = _Run("m_o", Principal("acme_labs", "bo@xifin.example"), conversation_id="c9")
    for run in [*runs, other]:
        app.state.runs[run.message_id] = run
    client.delete("/api/conversations", headers=LABS)
    assert all(r.discarded.is_set() for r in runs) and not other.discarded.is_set()


def test_lifespan_runs_a_startup_sweep():
    store = InMemoryTranscriptStore()
    store.append_message(A, "c1", MSG)
    store.set_retention(A, 1)
    _age(store, A, "c1", 2)
    app = create_app(settings=Settings(heartbeat_seconds=5, transcript_sweep_seconds=3600), store=store)
    with TestClient(app):
        pass
    assert store.get_conversation(A, "c1") is None


def test_sweep_disabled_with_zero():
    store = InMemoryTranscriptStore()
    store.append_message(A, "c1", MSG)
    store.set_retention(A, 1)
    _age(store, A, "c1", 2)
    with TestClient(create_app(settings=Settings(heartbeat_seconds=5, transcript_sweep_seconds=0), store=store)):
        pass
    assert store._items  # untouched (an explicit list/get would still expire it)
