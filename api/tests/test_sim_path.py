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


def _script():
    return ScriptedLLM([
        {"tool_calls": [{"name": "run_sql", "arguments": {"sql": SQL}}]},
        {"tool_calls": [{"name": "request_escalation", "arguments": {"reason": "want lossless"}}]},
        {"tool_calls": [{"name": "final_answer", "arguments": {"sql": SQL, "answer": "Distinct claims."}}]},
    ])


def test_sim_auth_verifies_cognito_tokens(world):
    auth = SimAuth(world.aws, world.dep)
    ctx = auth.authenticate(login(world.aws, "ana@xifin.example"), "r1")
    assert ctx.principal.customer == "acme_labs"
    for bad in ["", "not-a-jwt", login(world.aws, "ana@xifin.example")[:-4] + "AAAA"]:
        with pytest.raises(AuthError):
            auth.authenticate(bad, "r2")


def test_chat_over_query_path_is_scoped_and_never_escalates(world, monkeypatch):
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
        escalation = [p for p in parts(evs) if p["type"] == "tool_call" and p["tool"] == "request_escalation"]
        assert escalation and escalation[0]["state"] == "error"
        csv = client.get(f"/api/download/{table['csvFileId']}", headers=headers)
        assert csv.status_code == 200 and csv.text.startswith("claims\r\n")
    assert calls and not any(calls)
