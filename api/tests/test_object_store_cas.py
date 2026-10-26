"""Persistence without DynamoDB: conversation objects updated with If-Match compare-and-set, run events in numbered
write-once chunks replayed from Last-Event-ID. Every blob store runs the same checks, the S3 one on ccsim's S3."""
import threading

import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.store import Principal
from api.tests.conftest import LABS, events
from api.transcripts import DirBlobs, Keyring, MemoryBlobs, PreconditionFailed, S3Blobs, SealedTranscriptStore

ANA = Principal("acme_labs", "ana@xifin.example")
BO = Principal("acme_labs", "bo@xifin.example")
POLICY_VERSION = "2012-10-17"


@pytest.fixture
def simulated_s3_blobs(tmp_path):
    from ccsim.aws import ACCOUNT, LocalAWS, service_principal
    aws = LocalAWS(tmp_path / "aws")
    aws.s3.create_bucket("cc-state", versioning="Enabled")
    aws.iam.create_role("chat-api", {"Version": POLICY_VERSION, "Statement": [
        {"Effect": "Allow", "Principal": {"Service": "ecs-tasks.amazonaws.com"}, "Action": "sts:AssumeRole"}]})
    aws.iam.put_role_policy("chat-api", "state", {"Version": POLICY_VERSION, "Statement": [
        {"Effect": "Allow", "Action": ["s3:*"], "Resource": ["arn:aws:s3:::cc-state", "arn:aws:s3:::cc-state/*"]}]})
    session = aws.sts.assume_role(service_principal("ecs-tasks.amazonaws.com"), None,
                                  f"arn:aws:iam::{ACCOUNT}:role/chat-api", "chat-api-task")
    yield S3Blobs(aws.client("s3", session), "cc-state")
    aws.shutdown()


@pytest.fixture(params=["memory", "directory", "simulated_s3"])
def blobs(request, tmp_path):
    if request.param == "memory":
        return MemoryBlobs()
    if request.param == "directory":
        return DirBlobs(tmp_path / "blobs")
    return request.getfixturevalue("simulated_s3_blobs")


def test_conditional_writes(blobs):
    blobs.put("c/one", b"v1", if_none_match=True)
    with pytest.raises(PreconditionFailed):
        blobs.put("c/one", b"v2", if_none_match=True)
    body, etag = blobs.get_with_etag("c/one")
    assert body == b"v1"
    blobs.put("c/one", b"v2", if_match=etag)
    with pytest.raises(PreconditionFailed):
        blobs.put("c/one", b"v3", if_match=etag)            # stale ETag: the lost update is refused
    with pytest.raises(PreconditionFailed):
        blobs.put("c/missing", b"x", if_match=etag)
    assert blobs.get("c/one") == b"v2" and blobs.get_with_etag("c/missing") is None
    assert blobs.list("c/") == ["c/one"]


def test_concurrent_writers_lose_no_message(blobs, tmp_path):
    """Two API tasks (two store objects, no shared lock) append to one conversation at once."""
    keyring = Keyring(tmp_path / "keys")
    tasks = [SealedTranscriptStore(blobs, keyring), SealedTranscriptStore(blobs, keyring)]
    tasks[0].append_message(ANA, "c1", {"id": "m_first", "role": "user", "text": "first"})

    def append_many(task_index):
        for n in range(8):
            tasks[task_index].append_message(ANA, "c1", {"id": f"m{task_index}_{n}", "role": "user", "text": "x"})

    threads = [threading.Thread(target=append_many, args=(i,)) for i in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    ids = {m["id"] for m in tasks[1].get_conversation(ANA, "c1")["messages"]}
    assert ids == {"m_first"} | {f"m{i}_{n}" for i in range(2) for n in range(8)}


def test_a_write_between_read_and_write_is_retried_not_lost():
    blobs = MemoryBlobs()
    store = SealedTranscriptStore(blobs, Keyring())
    store.append_message(ANA, "c1", {"id": "m1", "role": "user", "text": "a"})
    other_task = SealedTranscriptStore(blobs, store.keyring)
    original_get = blobs.get_with_etag
    interleaved = []

    def get_then_let_another_task_write(key):
        found = original_get(key)
        if key.endswith("/content/c1.bin") and not interleaved:
            interleaved.append(True)
            other_task.append_message(ANA, "c1", {"id": "m_other", "role": "user", "text": "b"})
        return found

    blobs.get_with_etag = get_then_let_another_task_write
    store.update_conversation(ANA, "c1", title="Renamed")
    conversation = store.get_conversation(ANA, "c1")
    assert conversation["title"] == "Renamed" and [m["id"] for m in conversation["messages"]] == ["m1", "m_other"]


def test_run_event_chunks_are_write_once_scoped_and_deleted_with_the_conversation(blobs):
    store = SealedTranscriptStore(blobs, Keyring())
    store.append_message(ANA, "c1", {"id": "u1", "role": "user", "text": "q"})
    store.put_run_events(ANA, "c1", "m_run", 1, [{"seq": 2, "type": "status"}])
    store.put_run_events(ANA, "c1", "m_run", 0, [{"seq": 0, "type": "message.start"}, {"seq": 1, "type": "status"}])
    assert [e["seq"] for e in store.run_events(ANA, "m_run")] == [0, 1, 2]
    with pytest.raises(PreconditionFailed):
        store.put_run_events(ANA, "c1", "m_run", 1, [{"seq": 9, "type": "status"}])
    assert store.run_events(BO, "m_run") is None and store.run_events(ANA, "m_other") is None
    assert not any(b"message.start" in (blobs.get(k) or b"") for k in blobs.list(""))  # sealed at rest
    store.delete_conversation(ANA, "c1")
    assert store.run_events(ANA, "m_run") is None


def test_another_api_task_replays_a_run_from_last_event_id(tmp_path):
    blobs = DirBlobs(tmp_path / "blobs")
    keyring = Keyring(tmp_path / "keys")
    first_task = TestClient(create_app(settings=Settings(heartbeat_seconds=5),
                                       store=SealedTranscriptStore(blobs, keyring)))
    response = first_task.post("/api/chat", headers=LABS,
                               json={"messages": [{"role": "user", "text": "How many claims did we submit?"}]})
    original = events(response)
    message_id = original[0]["messageId"]
    second_task = TestClient(create_app(settings=Settings(heartbeat_seconds=5),
                                        store=SealedTranscriptStore(blobs, keyring)))
    replayed = events(second_task.get(f"/api/chat/{message_id}/stream",
                                      headers={**LABS, "Last-Event-ID": f"{message_id}:3"}))
    assert [e["seq"] for e in replayed] == [e["seq"] for e in original if e["seq"] > 3]
    assert replayed[-1]["type"] == "message.end"
    via_post = events(second_task.post("/api/chat", headers={**LABS, "Last-Event-ID": f"{message_id}:3"},
                                       json={"messages": []}))
    assert via_post == replayed
    other_user = {"Authorization": "Bearer demo:acme_labs:bo@xifin.example"}
    assert second_task.get(f"/api/chat/{message_id}/stream", headers=other_user).status_code == 404
