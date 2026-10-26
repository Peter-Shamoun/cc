"""Contract 1.17.0: pin / archive persisted server-side (PATCH flags, ConversationSummary flags, ?archived= filter)."""
import time

import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.store import Principal
from api.tests.conftest import DX, LABS, ask
from api.tests.test_titles import _validator
from api.transcripts import DirBlobs, Keyring, SealedTranscriptStore, make_store

COLLEAGUE = {"Authorization": "Bearer demo:acme_labs:bo@xifin.example"}


@pytest.fixture(params=["ephemeral", "persisted"])
def api(request):
    app = create_app(settings=Settings(heartbeat_seconds=5), store=make_store(request.param))
    return app, TestClient(app)


def _new(client, text="Paid amount by payer", headers=LABS):
    _, evs = ask(client, text, headers=headers)
    return evs[0]["conversationId"]


def _patch(client, cid, headers=LABS, **body):
    return client.patch(f"/api/conversations/{cid}", headers=headers, json=body)


def _ids(client, archived=None, headers=LABS):
    url = "/api/conversations" + (f"?archived={archived}" if archived else "")
    r = client.get(url, headers=headers)
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]], {i["id"]: i for i in r.json()["items"]}


def test_summaries_always_carry_flags_and_validate(api):
    _, client = api
    cid = _new(client)
    _, items = _ids(client)
    assert items[cid]["pinned"] is False and items[cid]["archived"] is False
    _validator("ConversationPage").validate(client.get("/api/conversations", headers=LABS).json())
    conv = client.get(f"/api/conversations/{cid}", headers=LABS).json()
    assert conv["pinned"] is False and conv["archived"] is False
    _validator("Conversation").validate(conv)


def test_pin_moves_to_top_without_touching_updated_at(api):
    _, client = api
    a = _new(client)
    b = _new(client)
    c = _new(client)
    order, items = _ids(client)
    assert order == [c, b, a]
    before = items[a]["updatedAt"]
    r = _patch(client, a, pinned=True)
    assert r.status_code == 200
    body = r.json()
    _validator("RenameResponse").validate(body)
    assert body["pinned"] is True and body["archived"] is False and body["updatedAt"] == before
    time.sleep(0.01)
    _patch(client, b, pinned=True)
    order, items = _ids(client)
    assert order == [b, a, c]  # most recently pinned first, then the rest newest first
    assert items[a]["updatedAt"] == before
    _patch(client, b, pinned=False)
    assert _ids(client)[0] == [a, c, b]


def test_archive_filters_and_drops_pin(api):
    _, client = api
    a = _new(client)
    b = _new(client)
    _patch(client, a, pinned=True)
    body = _patch(client, a, archived=True).json()
    assert body["archived"] is True and body["pinned"] is False  # archiving drops the pin
    assert _ids(client, "exclude")[0] == [b]
    assert _ids(client, "only")[0] == [a]
    assert set(_ids(client)[0]) == {a, b}  # default include (pre-1.17 clients see everything)
    # still searchable and openable
    hits = client.post("/api/conversations/search", headers=LABS, json={"q": "Paid"}).json()["items"]
    assert a in {h["id"] for h in hits}
    hits = client.post("/api/conversations/search", headers=LABS, json={"q": "Paid", "archived": "exclude"}).json()
    assert [h["id"] for h in hits["items"]] == [b]
    assert client.get(f"/api/conversations/{a}", headers=LABS).json()["archived"] is True
    # unarchive
    assert _patch(client, a, archived=False).json()["archived"] is False
    assert set(_ids(client, "exclude")[0]) == {a, b}


def test_pinning_an_archived_chat_unarchives_it(api):
    _, client = api
    a = _new(client)
    _patch(client, a, archived=True)
    body = _patch(client, a, pinned=True).json()
    assert body == {**body, "pinned": True, "archived": False}
    assert _ids(client, "exclude")[0] == [a]


def test_new_question_unarchives_but_assistant_append_does_not(api):
    app, client = api
    a = _new(client)
    _patch(client, a, archived=True)
    ask(client, "And by month?", conversation_id=a)
    assert client.get(f"/api/conversations/{a}", headers=LABS).json()["archived"] is False
    # an assistant-only append (a run finishing after the user archived) keeps it archived
    _patch(client, a, archived=True)
    principal = Principal("acme_labs", "ana@xifin.example")
    app.state.store.append_message(principal, a, {"id": "m_late", "role": "assistant", "parts": [],
                                                  "createdAt": "2026-10-25T00:00:00Z"})
    assert client.get(f"/api/conversations/{a}", headers=LABS).json()["archived"] is True


@pytest.mark.parametrize("body", [{}, {"pinned": True, "archived": True}, {"pinned": "yes"}, {"starred": True},
                                  {"pinned": None, "archived": None}])
def test_bad_patches_are_422_validation(api, body):
    _, client = api
    a = _new(client)
    r = _patch(client, a, **body)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    _validator("HttpError").validate(r.json())
    _, items = _ids(client)
    assert items[a]["pinned"] is False and items[a]["archived"] is False


def test_bad_archived_filter_is_422(api):
    _, client = api
    r = client.get("/api/conversations?archived=all", headers=LABS)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    r = client.post("/api/conversations/search", headers=LABS, json={"q": "x", "archived": "all"})
    assert r.status_code == 422


def test_title_and_flags_in_one_patch_and_rename_still_works(api):
    _, client = api
    a = _new(client)
    body = _patch(client, a, title="Payer mix", pinned=True).json()
    assert body["title"] == "Payer mix" and body["pinned"] is True
    body = _patch(client, a, title="").json()  # 1.3 rename body unchanged; flags kept
    assert body["title"] == "New analysis" and body["pinned"] is True
    assert _patch(client, a, title="a\x00b").status_code == 422
    assert _ids(client)[1][a]["pinned"] is True


def test_flags_are_per_user_and_404_for_others(api):
    _, client = api
    a = _new(client)
    for h in (COLLEAGUE, DX):
        r = _patch(client, a, headers=h, pinned=True)
        assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert _patch(client, "c_missing", pinned=True).status_code == 404
    assert _ids(client)[1][a]["pinned"] is False


def test_idempotent_pin_keeps_its_place(api):
    _, client = api
    a, b = _new(client), _new(client)
    _patch(client, a, pinned=True)
    time.sleep(0.01)
    _patch(client, b, pinned=True)
    _patch(client, a, pinned=True)  # re-pinning does not move it to the top
    assert _ids(client)[0] == [b, a]
    assert _patch(client, a, archived=False).json()["archived"] is False  # unarchive a live chat: no-op


def test_pins_survive_pages(api):
    app, client = api
    app.state.store.page_size = 3
    ids = [_new(client) for _ in range(5)]
    _patch(client, ids[0], pinned=True)  # oldest chat, would be on page 2
    first = client.get("/api/conversations", headers=LABS).json()
    assert first["items"][0]["id"] == ids[0] and first["next"]
    rest = client.get(f"/api/conversations?cursor={first['next']}", headers=LABS).json()["items"]
    assert len(first["items"]) + len(rest) == 5 and ids[0] not in {i["id"] for i in rest}


def test_audit_has_flags_no_text(api):
    app, client = api
    a = _new(client, "Paid amount for Zelda Quixote")
    _patch(client, a, pinned=True, title="Zelda's chat")
    evs = [e for e in app.state.audit.events if e["event"] in ("conversation_flags", "conversation_renamed")]
    assert {e["event"] for e in evs} == {"conversation_flags", "conversation_renamed"}
    flags = next(e for e in evs if e["event"] == "conversation_flags")
    assert flags["pinned"] is True and flags["archived"] is False
    assert "Zelda" not in str(app.state.audit.events)


def test_persisted_flags_are_sealed_and_survive_restart(tmp_path):
    def store():
        return SealedTranscriptStore(DirBlobs(tmp_path / "blobs"), Keyring(tmp_path / "keys"))
    p = Principal("acme_labs", "ana@xifin.example")
    s = store()
    s.append_message(p, "c_1", {"id": "u_1", "role": "user", "text": "hello", "createdAt": "2026-10-25T00:00:00Z"})
    s.update_conversation(p, "c_1", pinned=True)
    again = store()
    (item,) = again.list_conversations(p)["items"]
    assert item["pinned"] is True and item["archived"] is False
    meta = b"".join(f.read_bytes() for f in (tmp_path / "blobs").rglob("*.json"))
    assert b"pinned" not in meta  # flags live in the sealed body, metadata carries nothing new


def test_capability_flag(client):
    caps = client.get("/api/capabilities", headers=LABS).json()
    assert caps["pinArchive"] is True
    _validator("Capabilities").validate(caps)
