"""Regression tests for the API security/correctness review: transcript store scans and races, id shape, malformed
input 500s, bounded audit memory, masked explore targets. Synthetic values only."""
import threading

import pytest
from fastapi.testclient import TestClient

from api.app import AUDIT_EVENTS_KEPT_IN_MEMORY, AuditLog, Settings, create_app
from api.live import masked_explore_target
from api.store import Principal
from api.tests.conftest import LABS, ask
from api.transcripts import (ConversationMissing, DirBlobs, Keyring, MemoryBlobs, PreconditionFailed,
                             SealedTranscriptStore)

ANA = Principal("acme", "ana")
BO = Principal("acme", "bo")


class CountingBlobs(MemoryBlobs):
    def __init__(self):
        super().__init__()
        self.listed_prefixes = []

    def list(self, prefix):
        self.listed_prefixes.append(prefix)
        return super().list(prefix)


def test_append_and_ownership_check_never_list_the_whole_bucket():
    blobs = CountingBlobs()
    store = SealedTranscriptStore(blobs, Keyring())
    for n in range(20):
        store.append_message(Principal("other_co", f"u{n}"), f"c_{n}", {"id": "m", "role": "user", "text": "x"})
    blobs.listed_prefixes.clear()
    store.append_message(ANA, "c_mine", {"id": "m", "role": "user", "text": "x"})
    store.append_message(ANA, "c_mine", {"id": "m2", "role": "user", "text": "y"})
    assert not store.owned_by_other(ANA, "c_mine") and store.owned_by_other(BO, "c_mine")
    assert "" not in blobs.listed_prefixes, blobs.listed_prefixes


def test_conversation_id_has_one_owner_and_is_released_on_delete():
    store = SealedTranscriptStore(MemoryBlobs(), Keyring())
    store.append_message(ANA, "c_shared", {"id": "m", "role": "user", "text": "x"})
    with pytest.raises(PermissionError):
        store.append_message(BO, "c_shared", {"id": "m", "role": "user", "text": "y"})
    assert store.delete_conversation(ANA, "c_shared")
    assert not store.owned_by_other(BO, "c_shared")
    store.append_message(BO, "c_shared", {"id": "m", "role": "user", "text": "y"})
    store.delete_all(BO)
    assert not store.owned_by_other(ANA, "c_shared")


def test_conversation_saved_before_owner_records_stays_its_owners():
    store = SealedTranscriptStore(MemoryBlobs(), Keyring())
    store.append_message(ANA, "c_old", {"id": "m", "role": "user", "text": "x"})
    store.blobs.delete("transcripts/conversation-owners/c_old.json")  # as written before owner records
    store.append_message(BO, "c_old", {"id": "m", "role": "user", "text": "y"})  # claims the id
    assert not store.owned_by_other(ANA, "c_old")
    store.append_message(ANA, "c_old", {"id": "m2", "role": "user", "text": "z"})
    assert len(store.get_conversation(ANA, "c_old")["messages"]) == 2


def test_chat_route_is_sync_so_store_calls_stay_off_the_event_loop():
    app = create_app(settings=Settings(heartbeat_seconds=5))
    route = next(r for r in app.routes if getattr(r, "path", "") == "/api/chat" and "POST" in r.methods)
    import inspect
    assert not inspect.iscoroutinefunction(route.endpoint)


def test_run_events_are_refused_once_the_conversation_is_gone():
    store = SealedTranscriptStore(MemoryBlobs(), Keyring())
    store.append_message(ANA, "c_1", {"id": "u1", "role": "user", "text": "q"})
    store.put_run_events(ANA, "c_1", "m_1", 0, [{"type": "message.start", "seq": 0}])
    assert store.delete_conversation(ANA, "c_1")
    with pytest.raises(ConversationMissing):
        store.put_run_events(ANA, "c_1", "m_2", 0, [{"type": "text.delta", "seq": 0, "delta": "x"}])
    assert store.run_events(ANA, "m_1") is None and store.run_events(ANA, "m_2") is None


def test_final_answer_does_not_recreate_a_deleted_conversation():
    for store in (SealedTranscriptStore(MemoryBlobs(), Keyring()),
                  create_app(settings=Settings(heartbeat_seconds=5)).state.store):
        store.append_message(ANA, "c_1", {"id": "u1", "role": "user", "text": "q"})
        store.delete_conversation(ANA, "c_1")
        store.append_message(ANA, "c_1", {"id": "m1", "role": "assistant", "parts": []}, create_if_missing=False)
        assert store.get_conversation(ANA, "c_1") is None


def test_delete_discards_runs_before_objects_and_blocks_later_chunks():
    """A run registered with the cap check is discarded by a delete even before its user message lands."""
    app = create_app(settings=Settings(heartbeat_seconds=5))
    client = TestClient(app)
    _, evs = ask(client, "How many claims?")
    conversation_id = evs[0]["conversationId"]
    run = app.state.runs[evs[0]["messageId"]]
    run.done = False  # pretend it is still writing
    assert client.delete(f"/api/conversations/{conversation_id}", headers=LABS).json()["deleted"] is True
    assert run.discarded.is_set()
    assert run.run_event_chunk_lock.acquire(blocking=False)  # forget() released it
    run.run_event_chunk_lock.release()


def test_concurrent_first_writes_share_one_data_key():
    blobs, keyring = MemoryBlobs(), Keyring()
    task_a, task_b = SealedTranscriptStore(blobs, keyring), SealedTranscriptStore(blobs, keyring)
    original_get = blobs.get
    barrier = threading.Barrier(2)

    def racing_get(key):
        value = original_get(key)
        if key.endswith("dek.bin") and value is None and threading.current_thread().name in ("A", "B"):
            try:
                barrier.wait(timeout=1)
            except threading.BrokenBarrierError:
                pass
        return value

    blobs.get = racing_get
    threads = [threading.Thread(target=task.append_message, args=(ANA, cid, {"id": "1", "role": "user", "text": "a"}),
                                name=name) for task, cid, name in ((task_a, "c_a", "A"), (task_b, "c_b", "B"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    blobs.get = original_get
    assert sorted(c["id"] for c in task_a.list_conversations(ANA)["items"]) == ["c_a", "c_b"]


def test_dek_create_is_conditional():
    blobs = MemoryBlobs()
    store = SealedTranscriptStore(blobs, Keyring())
    first = store._dek(ANA, create=True)
    with pytest.raises(PreconditionFailed):
        blobs.put(f"{store._prefix(ANA)}dek.bin", b"x", if_none_match=True)
    assert store._dek(ANA, create=True) == first


def test_keyring_lost_create_race_uses_the_key_on_disk(tmp_path, monkeypatch):
    winner = Keyring(tmp_path)
    loser = Keyring(tmp_path)
    real_exists = type(tmp_path).exists
    # the loser checks before the winner writes, then its O_EXCL create fails
    monkeypatch.setattr(type(tmp_path), "exists", lambda self: False if self.suffix == ".kek" else real_exists(self))
    winner_key = winner.key("acme")
    assert loser.key("acme") == winner_key


@pytest.mark.parametrize("bad_id", [".", "..", "-x", ".hidden"])
def test_ids_must_start_alphanumeric(tmp_path, bad_id):
    store = SealedTranscriptStore(DirBlobs(tmp_path), Keyring())
    with pytest.raises(ValueError):
        store.append_message(ANA, bad_id, {"id": "u1", "role": "user", "text": "q"})
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5)))
    r = client.post("/api/chat", json={"conversationId": bad_id, "messages": [{"role": "user", "text": "q"}]},
                    headers=LABS)
    assert r.status_code == 422


@pytest.mark.parametrize("options", [{"strategy": ["auto"]}, {"model": {"id": "auto"}}, {"strategy": 1}])
def test_non_string_options_are_422_not_500(options):
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5)), raise_server_exceptions=False)
    r = client.post("/api/chat", json={"messages": [{"role": "user", "text": "q"}], "options": options}, headers=LABS)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"


def test_huge_last_event_id_is_treated_as_invalid():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5)), raise_server_exceptions=False)
    huge = "m_x:" + "9" * 5000
    r = client.post("/api/chat", json={"messages": []}, headers={**LABS, "Last-Event-ID": huge})
    assert r.status_code == 404 and r.json()["error"]["code"] == "stream_gone"
    r = client.get("/api/chat/m_x/stream", headers={**LABS, "Last-Event-ID": huge})
    assert r.status_code == 404


def test_audit_log_memory_is_bounded():
    audit = AuditLog()
    for n in range(AUDIT_EVENTS_KEPT_IN_MEMORY + 50):
        audit("chat_message", row_count=n)
    assert len(audit.events) == AUDIT_EVENTS_KEPT_IN_MEMORY and audit.events[-1]["row_count"] == n


def test_explore_target_is_masked():
    assert masked_explore_target("sem.claims") == "sem.claims"
    shown = masked_explore_target("claims for 123-45-6789 jane.t@example.com XYZ123456789 'Testperson'")
    assert not any(v in shown for v in ("123-45-6789", "jane.t@example.com", "XYZ123456789", "Testperson"))


def test_retention_change_on_another_task_applies_after_the_cache_window(monkeypatch):
    import api.transcripts as transcripts
    blobs, keyring = MemoryBlobs(), Keyring()
    task_a, task_b = SealedTranscriptStore(blobs, keyring), SealedTranscriptStore(blobs, keyring)
    assert task_a.retention_days(ANA) == 90
    task_b.set_retention(ANA, 7)
    monkeypatch.setattr(transcripts, "RETENTION_CACHE_SECONDS", -1)
    assert task_a.retention_days(ANA) == 7
