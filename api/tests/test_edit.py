"""Contract 1.2.0: `editOf` makes an edit a sibling of the edited user message, including the first question."""
from api.contract import validate_event
from api.tests.conftest import DX, LABS, events


def _send(client, text, conversation_id=None, headers=LABS, **msg):
    body = {"messages": [{"role": "user", "text": text, **msg}]}
    if conversation_id:
        body["conversationId"] = conversation_id
    return client.post("/api/chat", json=body, headers=headers)


def _conv(client, cid):
    return client.get(f"/api/conversations/{cid}", headers=LABS).json()["messages"]


def test_edit_first_question_is_a_new_root(client):
    first = events(_send(client, "How many claims?", id="u_first"))
    cid = first[0]["conversationId"]
    events(_send(client, "Paid amount by payer", cid, id="u_second", parentId=first[-1]["messageId"]))
    edited = events(_send(client, "How many denied claims?", cid, id="u_edit", editOf="u_first"))
    start = edited[0]
    validate_event(start)
    assert start["editOf"] == "u_first" and start["parentId"] == "u_edit"
    assert edited[-1]["type"] == "message.end"
    stored = {m["id"]: m for m in _conv(client, cid)}
    assert stored["u_edit"]["editOf"] == "u_first"
    assert "parentId" not in stored["u_edit"], "editing the first question makes a new root"


def test_edit_later_question_copies_the_edited_parent(client):
    first = events(_send(client, "How many claims?", id="u_1"))
    cid, a1 = first[0]["conversationId"], first[-1]["messageId"]
    events(_send(client, "Paid amount by payer", cid, id="u_2", parentId=a1))
    events(_send(client, "Paid amount by month", cid, id="u_3", editOf="u_2", parentId="u_bogus"))
    stored = {m["id"]: m for m in _conv(client, cid)}
    assert stored["u_3"]["parentId"] == a1 and stored["u_3"]["editOf"] == "u_2"


def test_edit_of_unknown_or_foreign_message_is_404(client):
    first = events(_send(client, "How many claims?", id="u_1"))
    cid = first[0]["conversationId"]
    r = _send(client, "x", cid, editOf="u_nope")
    assert r.status_code == 404 and r.json()["error"] == {"code": "not_found", "message": "Nothing to edit.",
                                                           "legacyCode": "validation"}   # 1.13.0
    # editOf must point at a user message
    r = _send(client, "x", cid, editOf=first[-1]["messageId"])
    assert r.status_code == 404
    # another tenant cannot target the conversation at all
    assert _send(client, "x", cid, headers=DX, editOf="u_1").status_code == 404
    # no conversationId -> nothing to edit
    assert _send(client, "x", editOf="u_1").status_code == 404
