"""Data escalation in chat on the demo backend (DemoAuth): the analyzer reads sem.* by default and may escalate to
lossless.* with a stated reason. The grant is for the token's customer, audited with a masked reason, shown as a
``request_escalation`` step and saved with the message. The SimAuth path is in test_sim_path.py."""
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.contract.conformance import check
from api.tests.conftest import DX, LABS, ask, cc, parts, scripted_analyzer

REJECTED_SQL = "SELECT count(*) AS rejected FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'"
REASON = "sem.claims has no file parse status; need rejected 837/835 files (ref ABC123456)"


def escalating_analyzer():
    return scripted_analyzer(cc("list"), cc("escalate", REASON), cc("list"),
                             cc("answer", "--sql", REJECTED_SQL, "--text", "Rejected files."))


def test_demo_escalation_is_granted_for_the_tokens_customer_and_saved():
    app = create_app(settings=Settings(heartbeat_seconds=5), analyzer=escalating_analyzer())
    client = TestClient(app)
    for headers, customer in ((LABS, "acme_labs"), (DX, "acme_diagnostics")):
        _, evs = ask(client, "How many delivered files were rejected?", headers=headers)
        assert evs[-1]["finishReason"] == "stop", evs
        assert check(evs) == []
        table = next(p for p in parts(evs) if p["type"] == "table")
        assert table["rowCount"] == 1 and table["rows"][0][0] > 0
        before, after = [e["summary"] for e in evs if e.get("probe") == "list_views"]
        assert before.startswith("Failed") and after == "1 views"          # lossless.* is listed only after
        (step,) = [e for e in evs if e.get("probe") == "request_escalation"]
        assert step["summary"] == "Reason: sem.claims has no file parse status; need rejected 837/835 files (ref …)"
        record = [e for e in app.state.audit.events if e["event"] == "data_escalation"][-1]
        assert record["customer"] == customer and record["reason"] == step["summary"].removeprefix("Reason: ")
        saved = client.get(f"/api/conversations/{evs[0]['conversationId']}", headers=headers).json()
        assert step in saved["messages"][-1]["steps"]


def test_demo_answers_without_escalating_when_sem_suffices():
    app = create_app(settings=Settings(heartbeat_seconds=5))           # the scripted demo model: sem.* only
    _, evs = ask(TestClient(app), "List all claims")
    assert evs[-1]["finishReason"] == "stop"
    assert not [e for e in evs if e.get("probe") == "request_escalation"]
    assert not [e for e in app.state.audit.events if e["event"] == "data_escalation"]
