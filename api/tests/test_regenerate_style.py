"""Contract 1.10.0: regenerate.style (CONTRACT.md §8.1)."""
import pytest

from api.styles import apply_style, first_sentence
from api.tests.conftest import LABS, events
from api.tests.test_titles import _validator


def _ask(client, scenario="table"):
    r = client.post("/api/chat", headers=LABS, json={"scenario": scenario, "messages": [
        {"id": "u1", "role": "user", "text": "Paid amount by payer"}]})
    return events(r)


def _regen(client, evs, **regen):
    body = {"conversationId": evs[0]["conversationId"], "messages": [],
            "regenerate": {"messageId": evs[0]["messageId"], **regen}}
    r = client.post("/api/chat", headers=LABS, json=body)
    return r, (events(r) if r.status_code == 200 else None)


def _text(evs):
    return "".join(e["delta"] for e in evs if e["type"] == "text.delta")


def _ptypes(evs):
    return [e["part"]["type"] for e in evs if e["type"] == "part"]


def test_concise_is_a_new_version_and_keeps_data_and_caveats(client):
    evs = _ask(client, "chart")
    r, again = _regen(client, evs, style="concise")
    assert r.status_code == 200
    start = again[0]
    _validator("MessageStart").validate(start)
    assert start["regenerateOf"] == evs[0]["messageId"] and start["regenerateStyle"] == "concise"
    assert "chart" in _ptypes(evs) and "chart" not in _ptypes(again)  # the chart duplicated the table
    for kind in ("code", "table", "callout"):
        assert _ptypes(again).count(kind) == _ptypes(evs).count(kind)
    assert len(_text(again)) <= len(_text(evs))
    conv = client.get(f"/api/conversations/{start['conversationId']}", headers=LABS).json()
    stored = [m for m in conv["messages"] if m["role"] == "assistant"]
    assert len(stored) == 2 and stored[1]["regenerateStyle"] == "concise" and stored[1]["regenerateOf"] == evs[0]["messageId"]
    for m in stored:
        _validator("StoredMessage").validate(m)


def test_detailed_adds_how_it_was_computed_without_row_values(client):
    evs = _ask(client)
    _, again = _regen(client, evs, style="detailed")
    text = _text(again)
    assert text.startswith(_text(evs).strip()) and "**How this was computed.**" in text
    table = next(e["part"] for e in again if e["type"] == "part" and e["part"]["type"] == "table")
    for row in table["rows"]:
        for cell in row:
            if isinstance(cell, str) and len(cell) > 3:
                assert cell not in text  # metadata only, never values
    assert _ptypes(again) == _ptypes(evs)


def test_default_and_missing_style_are_a_plain_rerun(client):
    evs = _ask(client)
    for regen in ({}, {"style": "default"}):
        _, again = _regen(client, evs, **regen)
        assert "regenerateStyle" not in again[0] and _text(again) == _text(evs)


def test_unknown_style_is_a_422_envelope(client):
    r, _ = _regen(client, _ask(client), style="shorter")
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"


@pytest.mark.parametrize("text,want", [
    ("Denials rose 12.5% in March. Payer A drove most of it.", "Denials rose 12.5% in March."),
    ("One line only", "One line only"),
    ("**7** payers. Next.", "**7** payers."),
    ("First para here.\n\nSecond para.", "First para here."),
])
def test_first_sentence(text, want):
    assert first_sentence(text) == want


def test_style_never_touches_non_stop_parts():
    parts = [{"type": "abstain", "id": "p1", "reason": "x"}]
    assert apply_style(parts, "concise") == parts and apply_style(parts, "detailed") == parts
