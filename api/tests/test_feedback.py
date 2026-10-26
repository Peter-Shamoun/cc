"""Contract 1.7.0: answer feedback (CONTRACT.md §13)."""
from api.tests.conftest import DX, LABS, ask
from api.tests.test_titles import _validator


def _answer(client):
    _, evs = ask(client, "Paid amount by payer", conversation_id="c_fb1")
    return evs[0]["messageId"]


def url(mid, cid="c_fb1"):
    return f"/api/conversations/{cid}/messages/{mid}/feedback"


def test_put_get_replace_and_clear(app, client):
    mid = _answer(client)
    r = client.put(url(mid), json={"rating": "down", "reasons": ["wrong_numbers", "wrong_numbers"],
                                   "comment": "  totals off  "}, headers=LABS)
    assert r.status_code == 200, r.text
    fb = r.json()
    assert fb["messageId"] == mid and fb["rating"] == "down" and fb["reasons"] == ["wrong_numbers"]
    assert fb["comment"] == "totals off"
    _validator("Feedback").validate(fb)
    stored = [m for m in client.get("/api/conversations/c_fb1", headers=LABS).json()["messages"] if m["id"] == mid][0]
    assert stored["feedback"]["rating"] == "down"
    _validator("StoredMessage").validate(stored)
    assert client.put(url(mid), json={"rating": "up"}, headers=LABS).json()["reasons"] == []
    assert client.delete(url(mid), headers=LABS).json() == {"cleared": True}
    stored = [m for m in client.get("/api/conversations/c_fb1", headers=LABS).json()["messages"] if m["id"] == mid][0]
    assert "feedback" not in stored
    # the audit trail has rating + reason ids only, never the comment
    events = [e for e in app.state.audit.events if e["event"] == "answer_feedback"]
    assert [e["kind"] for e in events] == ["down", "up", "cleared"] and events[0]["reason"] == "wrong_numbers"
    assert all("totals off" not in str(e) for e in events)


def test_validation_and_ownership(client):
    mid = _answer(client)
    bad = [{"rating": "meh"}, {"rating": "up", "reasons": ["wrong_numbers"]}, {"rating": "down", "reasons": ["nope"]},
           {"rating": "down", "comment": "x" * 1001}, {"rating": "up", "extra": 1, "reasons": "correct"}]
    for body in bad:
        assert client.put(url(mid), json=body, headers=LABS).status_code == 422, body
    assert client.put(url("u1"), json={"rating": "up"}, headers=LABS).status_code == 422  # a question, not an answer
    assert client.put(url("m_missing"), json={"rating": "up"}, headers=LABS).status_code == 404
    assert client.put(url(mid), json={"rating": "up"}, headers=DX).status_code == 404  # someone else's
    assert client.put(url(mid), json={"rating": "up"}).status_code == 401


def test_capabilities_list_reasons(client):
    caps = client.get("/api/capabilities").json()
    ids = {r["id"]: r["rating"] for r in caps["feedbackReasons"]}
    assert ids["correct"] == "up" and ids["wrong_numbers"] == "down" and ids["other"] == "down"
    _validator("Capabilities").validate(caps)
    v = _validator("FeedbackRequest")
    v.validate({"rating": "down", "reasons": ["other"], "comment": "x"})
    assert not v.is_valid({"rating": "down", "extra": True})


def test_both_stores_seal_and_scope_feedback():
    from api.store import InMemoryTranscriptStore, Principal
    from api.tests.test_transcripts import sealed

    a, b = Principal("acme_labs", "ana@xifin.example"), Principal("acme_labs", "bo@xifin.example")
    for store in (InMemoryTranscriptStore(), sealed()):
        store.append_message(a, "c_1", {"id": "u1", "role": "user", "text": "q"})
        store.append_message(a, "c_1", {"id": "m_1", "role": "assistant", "parts": []})
        fb = {"rating": "down", "reasons": ["other"], "comment": "Synthetic note 4711", "updatedAt": "z"}
        assert store.set_feedback(a, "c_1", "m_1", fb) == "ok"
        assert store.set_feedback(a, "c_1", "u1", fb) == "not_assistant"
        assert store.set_feedback(b, "c_1", "m_1", fb) == "missing"
        assert store.get_conversation(a, "c_1")["messages"][1]["feedback"] == fb
        if hasattr(store, "blobs"):  # sealed: the comment is ciphertext at rest
            raw = b"".join(store.blobs.get(k) or b"" for k in store.blobs.list(""))
            assert b"Synthetic note 4711" not in raw
        assert store.set_feedback(a, "c_1", "m_1", None) == "ok"
        assert "feedback" not in store.get_conversation(a, "c_1")["messages"][1]


def test_published_feedback_example_matches_server():
    import json
    from pathlib import Path

    from api.contract.gen_examples import feedback_example
    published = json.loads((Path(__file__).parents[1] / "contract/examples/feedback.json").read_text())
    assert published == feedback_example()
    _validator("FeedbackRequest").validate(published["request"])
    _validator("Feedback").validate(published["response"])
