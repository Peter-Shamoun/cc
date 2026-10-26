"""Contract 1.15.0 share security (#241): S1 rotate on widening, S4 expiry, QA-S18 SQL withheld when data is off."""
import json

import pytest

from api.share import DEFAULT_EXPIRY_DAYS, EXPIRY_DAYS, ShareRegistry, shared_view
from api.tests.conftest import LABS, events
from api.tests.test_titles import _validator

COLLEAGUE = {"Authorization": "Bearer demo:acme_labs:bo@xifin.example"}
DAY = 86400.0


def _answer(client, scenario="chart"):
    r = client.post("/api/chat", headers=LABS, json={"scenario": scenario, "messages": [
        {"id": "u1", "role": "user", "text": "Paid amount by service month"}]})
    return events(r)[0]["conversationId"]


def _put(client, cid, **body):
    return client.put(f"/api/conversations/{cid}/share", headers=LABS, json=body)


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture
def clock(app):
    c = Clock()
    app.state.shares.clock = c
    return c


# ---------------------------------------------------------------- S1: rotate on widening, keep on narrowing

def test_widening_rotates_and_revokes_narrowing_keeps(client, clock):
    cid = _answer(client)
    a = _put(client, cid).json()
    assert "rotated" not in a
    b = _put(client, cid, includeData=True).json()
    _validator("ShareLink").validate(b)
    assert b["shareId"] != a["shareId"] and b["rotated"] is True and b["url"] == f"/shared/{b['shareId']}"
    assert client.get(f"/api/shared/{a['shareId']}", headers=COLLEAGUE).status_code == 404
    assert client.get(f"/api/shared/{b['shareId']}", headers=COLLEAGUE).status_code == 200
    c = _put(client, cid, includeData=False).json()              # narrowing keeps the id
    assert c["shareId"] == b["shareId"] and c["includeData"] is False and "rotated" not in c
    d = _put(client, cid, includeData=False).json()              # unchanged keeps it
    assert d["shareId"] == b["shareId"]
    e = _put(client, cid, includeData=True).json()               # widening again rotates again
    assert e["shareId"] != b["shareId"] and e["rotated"] is True
    f = _put(client, cid, includeData=True).json()               # already wide: same id, not rotated
    assert f["shareId"] == e["shareId"] and "rotated" not in f
    assert client.get(f"/api/conversations/{cid}/share", headers=LABS).json()["shareId"] == e["shareId"]


def test_first_share_with_data_is_not_a_rotation(client, clock):
    link = _put(client, _answer(client), includeData=True).json()
    assert link["includeData"] is True and "rotated" not in link


def test_audit_records_rotation_without_ids(client, app, clock):
    cid = _answer(client)
    a = _put(client, cid).json()["shareId"]
    b = _put(client, cid, includeData=True).json()["shareId"]
    shared = [e for e in app.state.audit.events if e["event"] == "conversation_shared"]
    assert shared[-1]["rotated"] is True and shared[-1]["expires_in_days"] == 30
    log = json.dumps(app.state.audit.events)
    assert a not in log and b not in log


# ---------------------------------------------------------------- S4: expiry

def test_default_expiry_is_30_days_and_options_are_7_30_90(client, clock):
    link = _put(client, _answer(client)).json()
    assert link["expiresInDays"] == DEFAULT_EXPIRY_DAYS == 30 and EXPIRY_DAYS == (7, 30, 90)
    assert link["expiresAt"] == "2027-02-14T08:00:00Z"            # 1_800_000_000 + 30 days, UTC
    caps = client.get("/api/capabilities").json()
    assert caps["shareExpiry"] == {"options": [7, 30, 90], "default": 30}


@pytest.mark.parametrize("days", [7, 30, 90])
def test_owner_picks_expiry_and_it_expires_into_the_same_404(client, clock, days):
    cid = _answer(client)
    link = _put(client, cid, expiresInDays=days).json()
    assert link["expiresInDays"] == days
    sid = link["shareId"]
    view = client.get(f"/api/shared/{sid}", headers=COLLEAGUE)
    assert view.status_code == 200 and view.json()["expiresAt"] == link["expiresAt"]
    clock.t += days * DAY - 1
    assert client.get(f"/api/shared/{sid}", headers=COLLEAGUE).status_code == 200
    clock.t += 1
    expired = client.get(f"/api/shared/{sid}", headers=COLLEAGUE)
    unknown = client.get("/api/shared/sh_" + "x" * 24, headers=COLLEAGUE)
    assert expired.status_code == unknown.status_code == 404
    assert expired.json() == unknown.json()                          # indistinguishable from unknown/revoked
    assert client.get(f"/api/conversations/{cid}/share", headers=LABS).status_code == 404   # owner: not shared
    again = _put(client, cid).json()                                 # sharing again makes a fresh link
    assert again["shareId"] != sid and again["expiresInDays"] == 30


def test_put_without_expiry_keeps_it_and_with_expiry_restarts_it(client, clock):
    cid = _answer(client)
    a = _put(client, cid, expiresInDays=7).json()
    clock.t += DAY
    b = _put(client, cid, includeData=False).json()                  # no expiresInDays: unchanged
    assert b["expiresAt"] == a["expiresAt"] and b["expiresInDays"] == 7
    c = _put(client, cid, expiresInDays=90).json()                   # restarts from now
    assert c["shareId"] == a["shareId"] and c["expiresInDays"] == 90 and c["expiresAt"] > a["expiresAt"]
    d = _put(client, cid, includeData=True).json()                   # rotation without a choice: 30 days
    assert d["rotated"] is True and d["expiresInDays"] == 30


@pytest.mark.parametrize("bad", [0, 1, 14, 31, 365, -7, "30", None.__class__.__name__, 30.5])
def test_other_expiry_values_are_422(client, clock, bad):
    r = _put(client, _answer(client), expiresInDays=bad)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"


def test_registry_rejects_bad_expiry_and_purges():
    clock = Clock()
    reg = ShareRegistry(clock)
    with pytest.raises(ValueError):
        reg.put("c", "u", "conv", False, 14)
    s = reg.put("c", "u", "conv", False, 7)
    assert reg.get(s.share_id, "c") is s and reg.get(s.share_id, "other") is None
    clock.t += 7 * DAY
    assert reg.get(s.share_id, "c") is None and reg.for_conversation("c", "u", "conv") is None
    assert reg._by_id == {}


# ---------------------------------------------------------------- QA-S18: SQL withheld when data is off

def _sql_strings(view):
    out = []
    for m in view["messages"]:
        for p in m.get("parts", []):
            if p["type"] == "code" and p.get("language") == "sql":
                out.append(p["code"])
            if p["type"] == "tool_call":
                out.append(p.get("input", {}).get("sql", ""))
        for st in m.get("steps", []):
            out += [st.get("sql", ""), st.get("revisedSql", "")]
            out += [c.get("sql", "") for c in st.get("candidates", []) if isinstance(c, dict)]
    return [x for x in out if x]


@pytest.mark.parametrize("scenario", ["chart", "repair", "candidates", "escalation", "report"])
def test_data_off_view_withholds_sql_everywhere(client, clock, scenario):
    cid = _answer(client, scenario)
    own = client.get(f"/api/conversations/{cid}", headers=LABS).json()
    sid = _put(client, cid).json()["shareId"]
    view = client.get(f"/api/shared/{sid}", headers=COLLEAGUE).json()
    _validator("SharedConversation").validate(view)
    assert view["sqlWithheld"] is True and _sql_strings(view) == []
    assert "SELECT" not in json.dumps(view).upper().replace("SELECTED", "")
    parts = [p for m in view["messages"] for p in m.get("parts", [])]
    assert all("sqlPartId" not in p for p in parts)
    # the owner's own transcript is untouched
    assert _sql_strings({"messages": own["messages"]}) or scenario == "report"


def test_data_on_view_keeps_sql(client, clock):
    cid = _answer(client)
    sid = _put(client, cid, includeData=True).json()["shareId"]
    view = client.get(f"/api/shared/{sid}", headers=COLLEAGUE).json()
    assert "sqlWithheld" not in view and _sql_strings(view)


def test_withholding_covers_legacy_tool_calls_and_sql_artifacts():
    reg = ShareRegistry(Clock())
    share = reg.put("c", "u", "conv", False)
    conv = {"title": "t", "updatedAt": None, "messages": [{"id": "m", "role": "assistant", "parts": [
        {"type": "tool_call", "id": "t1", "tool": "run_sql", "label": "q", "state": "ok",
         "input": {"sql": "SELECT 1 FROM sem.claims WHERE member_id = 'Q'"}},
        {"type": "artifact", "id": "a1", "kind": "code", "title": "SQL",
         "content": {"type": "code", "id": "c", "language": "sql", "code": "SELECT 2"}},
        {"type": "code", "id": "c2", "language": "python", "code": "print(1)"},
        {"type": "code", "id": "c3", "language": "SQL", "code": "SELECT 6"},
        {"type": "table", "id": "tb", "columns": [], "rows": [[1]], "rowCount": 1, "sqlPartId": "c9"}],
        "steps": [{"type": "step.sql_running", "stepId": "s", "sql": "SELECT 3", "maskedLiterals": 1},
                  {"type": "step.repair_attempt", "stepId": "r", "revisedSql": "SELECT 4"},
                  {"type": "step.candidates", "stepId": "k", "candidates": [{"id": "a", "sql": "SELECT 5"}]}]}]}
    view = shared_view(share, conv)
    raw = json.dumps(view)
    assert all(f"SELECT {n}" not in raw for n in range(1, 7)) and "member_id" not in raw
    kinds = [p["type"] for p in view["messages"][0]["parts"]]
    assert kinds == ["tool_call", "code", "table"] and view["messages"][0]["parts"][1]["language"] == "python"
    assert "maskedLiterals" not in raw and view["sqlWithheld"] is True
    assert conv["messages"][0]["steps"][0]["sql"] == "SELECT 3"        # input not mutated
