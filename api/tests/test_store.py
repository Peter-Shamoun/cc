from api.store import DownloadRegistry, InMemoryTranscriptStore, Principal
from api.tests.conftest import DX, LABS, ask

A = Principal("acme_labs", "ana")
B = Principal("acme_diagnostics", "dev")


def test_in_memory_store_scopes_and_deletes():
    audit = []
    store = InMemoryTranscriptStore(audit=lambda event, **kw: audit.append((event, kw)))
    store.append_message(A, "c1", {"id": "m1", "role": "user", "text": "q"})
    assert store.get_conversation(B, "c1") is None
    assert store.list_conversations(B) == {"items": []}
    assert store.get_conversation(A, "c1")["messages"][0]["id"] == "m1"
    listed = store.list_conversations(A)["items"]
    assert listed[0]["id"] == "c1" and listed[0]["title"] == "New analysis"
    assert not store.delete_conversation(B, "c1")
    assert store.delete_conversation(A, "c1") and audit[0][0] == "conversation_deleted"
    assert store.get_conversation(A, "c1") is None


def test_retention_and_paging():
    store = InMemoryTranscriptStore()
    store.page_size = 2
    for i in range(3):
        store.append_message(A, f"c{i}", {"id": f"m{i}", "role": "user", "text": "q"})
    first = store.list_conversations(A)
    assert len(first["items"]) == 2 and first["next"] == "2"
    assert len(store.list_conversations(A, first["next"])["items"]) == 1
    store.set_retention(A, 0)
    assert store.list_conversations(A)["items"] == []


def test_download_registry_expiry():
    reg = DownloadRegistry(ttl_seconds=-1)
    item = reg.put(A, "result.csv", ["a"], [[1]])
    assert reg.get(A, item.id) is None


def test_chat_persists_both_turns(client):
    _, evs = ask(client, "How many claims?")
    cid = evs[0]["conversationId"]
    conv = client.get(f"/api/conversations/{cid}", headers=LABS).json()
    roles = [m["role"] for m in conv["messages"]]
    assert roles == ["user", "assistant"]
    assistant = conv["messages"][1]
    assert assistant["id"] == evs[0]["messageId"] and assistant["parts"]
    _, more = ask(client, "denial rate by payer", conversation_id=cid)
    assert more[0]["conversationId"] == cid
    assert len(client.get(f"/api/conversations/{cid}", headers=LABS).json()["messages"]) == 4
    assert client.get("/api/conversations", headers=LABS).json()["items"][0]["id"] == cid
    assert client.get("/api/conversations", headers=DX).json()["items"] == []
    assert client.delete(f"/api/conversations/{cid}", headers=LABS).json() == {"deleted": True}
