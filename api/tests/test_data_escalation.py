"""Data escalation in chat on the demo backend (DemoAuth): the analyzer reads sem.* by default and may escalate to
lossless.* with a stated reason. The grant is for the token's customer, audited with a masked reason, shown as a
``request_escalation`` step and saved with the message. The SimAuth paths are in test_sim_path.py (in-process) and
test_claude_code_cli_sim.py (claude_code_cli over the run channel)."""
from fastapi.testclient import TestClient

from analyzer.llm import ScriptedLLM
from api.app import Settings, create_app
from api.contract.conformance import check
from api.tests.conftest import DX, LABS, ask, parts
from api.tests.test_claude_code_cli_driver import (ESCALATION_REASON, driver_factory, escalation_plan,  # noqa: F401
                                                   tiny_snapshot)

REJECTED_SQL = "SELECT count(*) AS rejected FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'"
REASON = "sem.claims has no file parse status; need rejected 837/835 files (ref ABC123456)"


def escalating_script():
    return ScriptedLLM([
        {"tool_calls": [{"name": "list_relations", "arguments": {}}]},
        {"tool_calls": [{"name": "request_escalation", "arguments": {"reason": REASON}}]},
        {"tool_calls": [{"name": "list_relations", "arguments": {}}]},
        {"tool_calls": [{"name": "final_answer", "arguments": {"sql": REJECTED_SQL, "answer": "Rejected files."}}]},
    ])


def test_demo_in_process_escalation_is_granted_for_the_tokens_customer_and_saved():
    app = create_app(settings=Settings(heartbeat_seconds=5), llm_factory=escalating_script)
    client = TestClient(app)
    for headers, customer in ((LABS, "acme_labs"), (DX, "acme_diagnostics")):
        _, evs = ask(client, "How many delivered files were rejected?", headers=headers)
        assert evs[-1]["finishReason"] == "stop", evs
        assert check(evs) == []
        table = next(p for p in parts(evs) if p["type"] == "table")
        assert table["rowCount"] == 1 and table["rows"][0][0] > 0
        before, after = [e["summary"] for e in evs if e.get("probe") == "list_views"]
        assert (before, after) == ("1 views", "2 views")                  # lossless.* is listed only after
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


def test_demo_cli_escalation_is_audited_by_the_app(driver_factory):  # noqa: F811
    app = create_app(settings=Settings(heartbeat_seconds=5), claude_code_cli_driver=driver_factory(escalation_plan()))
    client = TestClient(app)
    _, evs = ask(client, "How many files were rejected?")
    assert evs[-1]["finishReason"] == "stop", evs
    assert check(evs) == []
    assert next(p for p in parts(evs) if p["type"] == "table")["rows"] == [[2]]
    (record,) = [e for e in app.state.audit.events if e["event"] == "data_escalation"]
    assert record["customer"] == "acme_labs" and record["user"] == "ana@xifin.example"
    assert "123-45-6789" not in record["reason"] and record["reason"].startswith("sem.* has no parse status")
