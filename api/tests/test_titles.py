"""Contract 1.3.0: PHI-free server titles (message.end.conversationTitle) and the conversation REST shapes."""
import json
from pathlib import Path

import jsonschema
import pytest

from api.tests.conftest import LABS, events
from api.titles import title_from_columns

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "contract" / "v1.schema.json").read_text())


def _validator(name):
    return jsonschema.Draft202012Validator({"$ref": f"#/$defs/{name}", "$defs": SCHEMA["$defs"]})


def col(key, type_="string", **kw):
    return {"key": key, "label": key, "type": type_, **kw}


@pytest.mark.parametrize("cols,want", [
    ([col("payer_name"), col("paid_amount", "currency"), col("claims", "integer")], "Paid amount by payer name"),
    ([col("carc_code"), col("claims", "integer")], "Claims by CARC code"),
    ([col("claims", "integer")], "Claims"),
    ([col("payer_name"), col("service_month", "date"), col("denial_rate", "percent")],
     "Denial rate by payer name and service month"),
])
def test_title_from_schema_identifiers(cols, want):
    assert title_from_columns(cols) == want


@pytest.mark.parametrize("cols", [
    [col("patient_name"), col("claims", "integer")],                 # PHI column
    [col("claims_for_jane_doe", "integer")],                         # model alias embedding a literal
    [col("payer_name", phi=True), col("claims", "integer")],         # flagged PHI
    [col("Paid Amount", "currency")],                                # not an identifier
    [col("payer_name")],                                             # no measure
    [],
])
def test_title_fails_closed(cols):
    assert title_from_columns(cols) is None


def _ask(client, text, cid=None):
    body = {"messages": [{"role": "user", "text": text}], **({"conversationId": cid} if cid else {})}
    return events(client.post("/api/chat", json=body, headers=LABS))


def test_first_answer_titles_the_conversation_once(client):
    evs = _ask(client, "Paid amount by payer")
    end, cid = evs[-1], evs[0]["conversationId"]
    assert end["conversationTitle"] == "Paid amount by payer name"
    conv = client.get(f"/api/conversations/{cid}", headers=LABS).json()
    assert conv["title"] == "Paid amount by payer name"
    _validator("Conversation").validate(conv)
    again = _ask(client, "Paid amount trend by month", cid)
    assert "conversationTitle" not in again[-1], "only the first answer titles a conversation"


def test_user_rename_wins(client):
    evs = _ask(client, "How bad are denials?")  # clarify: needs_input, no title yet
    cid = evs[0]["conversationId"]
    assert "conversationTitle" not in evs[-1]
    r = client.patch(f"/api/conversations/{cid}", json={"title": "My denials"}, headers=LABS)
    _validator("RenameResponse").validate(r.json())
    later = _ask(client, "Paid amount by payer", cid)
    assert "conversationTitle" not in later[-1]
    assert client.get(f"/api/conversations/{cid}", headers=LABS).json()["title"] == "My denials"


def test_rest_shapes_validate(client):
    _ask(client, "Paid amount by payer")
    _validator("ConversationPage").validate(client.get("/api/conversations", headers=LABS).json())
    found = client.post("/api/conversations/search", json={"q": "payer"}, headers=LABS).json()
    _validator("ConversationPage").validate(found)
    assert found["items"] and "snippet" in found["items"][0]
    _validator("TranscriptsInfo").validate(client.get("/api/transcripts", headers=LABS).json())
