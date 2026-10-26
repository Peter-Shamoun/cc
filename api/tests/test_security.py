"""PHI redaction, tenant binding, audited downloads, no escalation, no paid APIs, no data in logs."""
import logging

import pytest

from analyzer.tools import ToolError
from api.app import Settings, create_app
from api.backends import DemoAuth, ReasonedEscalationOpener
from api.tests.conftest import DX, LABS, ask, parts
from fastapi.testclient import TestClient


def test_phi_columns_are_redacted_and_flagged(client):
    _, evs = ask(client, "Which names go with each claim?")
    table = next(p for p in parts(evs) if p["type"] == "table")
    keys = [c["key"] for c in table["columns"]]
    i = keys.index("name_first")
    assert table["columns"][i]["phi"] is True
    assert all(row[i] is None for row in table["rows"])
    assert table["sensitivity"] == "limited"
    assert "Synthetic" not in str(evs)
    callout = next(p for p in parts(evs) if p["type"] == "callout" and p["variant"] == "phi_redaction")
    assert "name_first" in callout["items"][0]["text"]
    csv = client.get(f"/api/download/{table['csvFileId']}", headers=LABS).text
    assert "Synthetic" not in csv


def test_download_is_csv_audited_and_owner_bound(client, app):
    _, evs = ask(client, "list all claims")
    file_id = next(p for p in parts(evs) if p["type"] == "file")["fileId"]
    response = client.get(f"/api/download/{file_id}", headers=LABS)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert response.headers["cache-control"] == "no-store"
    lines = response.text.strip().split("\r\n")
    assert lines[0] == "claim_key,payer_name,service_date,status,charge_amount,paid_amount,carc_code"
    assert len(lines) == 1001   # B1's row limit: 1000 rows
    record = [e for e in app.state.audit.events if e["event"] == "download"][-1]
    assert record["row_count"] == 1000 and record["file_id"] == file_id and record["customer"] == "acme_labs"
    assert set(record) <= {"event", "at", "customer", "user", "file_id", "row_count"}
    assert client.get(f"/api/download/{file_id}", headers=DX).status_code == 404
    assert client.get(f"/api/download/{file_id}").status_code == 401
    assert client.get("/api/download/f_nope", headers=LABS).status_code == 404
    assert any(e["event"] == "download_denied" for e in app.state.audit.events)


def test_tenants_see_only_their_data_and_conversations(client):
    _, labs = ask(client, "list all claims")
    _, dx = ask(client, "list all claims", headers=DX)
    lab_keys = {r[0] for r in next(p for p in parts(labs) if p["type"] == "table")["rows"]}
    dx_keys = {r[0] for r in next(p for p in parts(dx) if p["type"] == "table")["rows"]}
    assert lab_keys and all(k.startswith("AL") for k in lab_keys)
    assert dx_keys and all(k.startswith("AD") for k in dx_keys)
    cid = labs[0]["conversationId"]
    assert client.get(f"/api/conversations/{cid}", headers=DX).status_code == 404
    r = client.post("/api/chat", json={"conversationId": cid, "messages": [{"role": "user", "text": "hi"}]}, headers=DX)
    assert r.status_code == 404


def test_escalation_needs_a_stated_reason_and_is_audited_masked():
    class Inner:
        opened = []

        def open(self, escalate):
            self.opened.append(escalate)
            return f"handle-{escalate}"

    inner, granted = Inner(), []
    opener = ReasonedEscalationOpener(inner, on_escalation_granted=granted.append)
    assert opener.open(False) == "handle-False"
    with pytest.raises(ToolError, match="stated reason"):
        opener.open(True)                       # no restoring an escalation that was never granted
    for blank in ("", "   ", None):
        with pytest.raises(ToolError, match="requires a reason"):
            opener.open_escalated(blank)
    assert inner.opened == [False] and not opener.escalated and not granted
    assert opener.open_escalated("need rejected files for member 123-45-6789 jane@x.com") == "handle-True"
    assert opener.escalated and opener.open(True) == "handle-True"
    assert granted == [opener.escalation_reason]
    assert "123-45-6789" not in granted[0] and "jane@x.com" not in granted[0] and "rejected files" in granted[0]


def test_no_row_data_or_sql_in_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    _, evs = ask(client, "list all claims")
    file_id = next(p for p in parts(evs) if p["type"] == "file")["fileId"]
    client.get(f"/api/download/{file_id}", headers=LABS)
    text = caplog.text
    assert "AL1" not in text and "Synthetic" not in text and "SELECT" not in text
    assert "list all claims" not in text


def test_auth_rejects_bad_tokens():
    auth = DemoAuth()
    from api.backends import AuthError
    for token in ["", "demo:acme_labs", "demo:other:x", "Bearer x", "demo:acme_labs:a b"]:
        with pytest.raises(AuthError):
            auth.authenticate(token, "r1")
