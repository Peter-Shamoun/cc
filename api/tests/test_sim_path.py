"""End to end over the real mock path: Cognito id token -> STS -> ccsim.query.open_session."""
import pytest
from fastapi.testclient import TestClient

from analyzer.llm import ScriptedLLM
from api.app import Settings, create_app
from api.backends import AuthError, SimAuth
from api.tests.conftest import ask, parts
from ccsim.edi_mock import Scenario
from ccsim.query import login
from ccsim.world import build_world

SQL = "SELECT COUNT(DISTINCT claim_key) AS claims FROM sem.claims"


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    built = build_world(tmp_path_factory.mktemp("api-world"),
                        Scenario(days=3, volume_scale=0.05, seed=4, burn_in_days=0, p_corrupt_file=1.0,
                                 p_duplicate_835_file=0.0))
    yield built
    built.aws.shutdown()


REJECTED_SQL = "SELECT count(*) AS rejected FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'"


def _script():
    return ScriptedLLM([
        {"tool_calls": [{"name": "run_sql", "arguments": {"sql": SQL}}]},
        {"tool_calls": [{"name": "final_answer", "arguments": {"sql": SQL, "answer": "Distinct claims."}}]},
    ])


def _escalating_script():
    return ScriptedLLM([
        {"tool_calls": [{"name": "list_relations", "arguments": {}}]},
        {"tool_calls": [{"name": "run_sql", "arguments": {"sql": REJECTED_SQL}}]},        # refused: not escalated
        {"tool_calls": [{"name": "request_escalation", "arguments": {"reason": ""}}]},    # refused: no reason
        {"tool_calls": [{"name": "request_escalation",
                         "arguments": {"reason": "sem.* has no file parse status (file 123-45-6789)"}}]},
        {"tool_calls": [{"name": "final_answer", "arguments": {"sql": REJECTED_SQL, "answer": "Rejected files."}}]},
    ])


def test_sim_auth_verifies_cognito_tokens(world):
    auth = SimAuth(world.aws, world.dep)
    ctx = auth.authenticate(login(world.aws, "ana@xifin.example"), "r1")
    assert ctx.principal.customer == "acme_labs"
    for bad in ["", "not-a-jwt", login(world.aws, "ana@xifin.example")[:-4] + "AAAA"]:
        with pytest.raises(AuthError):
            auth.authenticate(bad, "r2")


def test_chat_over_query_path_is_scoped_and_does_not_escalate_unasked(world, monkeypatch):
    import ccsim.query as query
    calls = []
    real = query.open_session

    def spy(aws, dep, token, escalate=False, **kw):
        calls.append(escalate)
        return real(aws, dep, token, escalate=escalate, **kw)

    monkeypatch.setattr(query, "open_session", spy)
    app = create_app(auth=SimAuth(world.aws, world.dep), settings=Settings(heartbeat_seconds=5), llm_factory=_script)
    client = TestClient(app)
    for upn, prefix in [("ana@xifin.example", "acme_labs"), ("dev@xifin.example", "acme_diagnostics")]:
        headers = {"Authorization": "Bearer " + login(world.aws, upn)}
        _, evs = ask(client, "How many claims did we submit?", headers=headers)
        assert evs[-1]["finishReason"] == "stop", evs
        table = next(p for p in parts(evs) if p["type"] == "table")
        assert table["rowCount"] == 1 and table["rows"][0][0] > 0
        assert not [e for e in evs if e.get("probe") == "request_escalation"]
        csv = client.get(f"/api/download/{table['csvFileId']}", headers=headers)
        assert csv.status_code == 200 and csv.text.startswith("claims\r\n")
    assert calls and not any(calls)


def test_in_process_escalation_is_reasoned_audited_and_same_customer(world, monkeypatch):
    import ccsim.query as query
    from api.contract.conformance import check
    calls = []
    real = query.open_session

    def spy(aws, dep, token, escalate=False, **kw):
        session = real(aws, dep, token, escalate=escalate, **kw)
        calls.append((session.customer, escalate))
        if escalate:   # no lossless.* column is named like a patient identifier
            from analyzer.tools import is_phi_column
            columns = session.con.execute("SELECT column_name FROM duckdb_columns() "
                                          "WHERE schema_name = 'lossless'").fetchall()
            assert columns and not [c for (c,) in columns if is_phi_column(c)]
        return session

    monkeypatch.setattr(query, "open_session", spy)
    app = create_app(auth=SimAuth(world.aws, world.dep), settings=Settings(heartbeat_seconds=5),
                     llm_factory=_escalating_script)
    client = TestClient(app)
    token = login(world.aws, "dev@xifin.example")
    expected = real(world.aws, world.dep, token, escalate=True).con.execute(REJECTED_SQL).fetchone()[0]
    calls.clear()
    _, evs = ask(client, "How many delivered files were rejected?", headers={"Authorization": "Bearer " + token})
    assert evs[-1]["finishReason"] == "stop", evs
    assert check(evs) == []
    assert next(p for p in parts(evs) if p["type"] == "table")["rows"] == [[expected]]
    assert calls == [("acme_diagnostics", False), ("acme_diagnostics", True)]
    (listed,) = [e for e in evs if e.get("probe") == "list_views"]
    assert listed["summary"] == "10 views"                                   # sem.* only before the escalation
    *refused, granted = [e for e in evs if e.get("probe") == "request_escalation"]
    assert [e["summary"].startswith("Failed") for e in refused] == [True]
    assert granted["summary"] == "Reason: sem.* has no file parse status (file …)"
    (record,) = [e for e in app.state.audit.events if e["event"] == "data_escalation"]
    assert record["customer"] == "acme_diagnostics"
    assert record["user"] == world.aws.cognito.verify_id_token(token)["sub"]
    assert record["reason"] == "sem.* has no file parse status (file …)"
