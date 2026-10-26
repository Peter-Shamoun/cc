"""BACKEND-FIT (Peter, 11:57 PM): the user never picks a database, lake, schema or view.

Production opens every session from the Cognito token alone (ccsim.query.open_session): the customer is the
``custom:customer`` claim, the user is ``email`` (or ``sub``), and the relations are fixed server-side (``sem.*``;
``lossless.*`` only when escalating, which chat never does). These tests pin that nothing a client sends (body
options, extra body fields, query parameters, headers) can choose the customer, the relations or escalation, in the
demo stub and on the real mock path, and that the demo stub derives identity the same way production does.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.backends import AuthError, DemoAuth, SimAuth
from api.tests.conftest import DX, LABS, events, parts

#: Everything a client might try in order to choose data. None of it may have any effect.
HINT_OPTIONS = {"customer": "acme_diagnostics", "tenant": "acme_diagnostics", "escalate": True, "lossless": True,
                "schema": "lossless", "database": "claims_lake", "lake": "raw", "scope": "lossless.*",
                "views": ["lossless.claims"], "relations": ["lossless.claims"], "sources": ["raw.x12_837"]}
HINT_FIELDS = {"customer": "acme_diagnostics", "tenant": "acme_diagnostics", "escalate": True,
               "scope": {"views": ["lossless.claims"]}, "dataSource": "raw"}
HINT_QUERY = "?customer=acme_diagnostics&tenant=acme_diagnostics&escalate=1&schema=lossless&scope=raw"
HINT_HEADERS = {"X-Customer": "acme_diagnostics", "X-Tenant": "acme_diagnostics", "X-Scope": "lossless.*",
                "X-Escalate": "1", "X-Database": "raw", "X-Forwarded-User": "dev@xifin.example"}


def _claims_table(client, headers, *, hints: bool):
    body = {"messages": [{"id": "u1", "role": "user", "text": "List all claims"}]}
    if hints:
        body = {**body, **HINT_FIELDS, "options": dict(HINT_OPTIONS)}
    r = client.post("/api/chat" + (HINT_QUERY if hints else ""), json=body,
                    headers={**headers, **(HINT_HEADERS if hints else {})})
    assert r.status_code == 200, r.text
    evs = events(r)
    assert evs[-1]["type"] == "message.end" and evs[-1]["finishReason"] == "stop", evs[-1]
    table = next(p for p in parts(evs) if p["type"] == "table")
    return evs, table


def test_demo_identity_comes_from_the_token_like_production():
    """custom:customer -> customer, email -> user: the demo token carries exactly those two claims."""
    ctx = DemoAuth().authenticate("demo:acme_labs:ana@xifin.example", "r1")
    assert (ctx.principal.customer, ctx.principal.user) == ("acme_labs", "ana@xifin.example")
    # Only customers the deployment knows (prod: a reader role and a manifest exist for the customer).
    for bad in ["demo:other_corp:ana@xifin.example", "demo:acme_labs", "Bearer demo:acme_labs:a", "", "demo:ACME:a"]:
        with pytest.raises(AuthError):
            DemoAuth().authenticate(bad, "r2")


def test_demo_session_exposes_only_sem_relations_and_never_escalates():
    ctx = DemoAuth().authenticate("demo:acme_labs:ana@xifin.example", "r1")
    with pytest.raises(Exception, match="escalation is not available"):
        ctx.opener.open(True)
    handle = ctx.opener.open(False)
    rows = handle.con.execute("SELECT DISTINCT table_schema FROM information_schema.tables").fetchall()
    assert {r[0] for r in rows} == {"sem"}


@pytest.mark.parametrize("headers, prefix", [(LABS, "AL"), (DX, "AD")])
def test_client_hints_cannot_choose_customer_relations_or_escalation(headers, prefix):
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5)))
    _, plain = _claims_table(client, headers, hints=False)
    evs, hinted = _claims_table(client, headers, hints=True)
    keys = [row[0] for row in hinted["rows"]]
    assert keys and all(k.startswith(prefix) for k in keys), keys[:5]  # the token's customer, whatever was asked
    assert hinted["rows"] == plain["rows"] and hinted["rowCount"] == plain["rowCount"]
    assert all(v.startswith("sem.") for v in (hinted.get("source") or {}).get("views", []))
    assert not [e for e in evs if e["type"] == "escalation"]


def test_downloads_and_shares_stay_inside_the_tokens_customer():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5)))
    evs, table = _claims_table(client, LABS, hints=False)
    conv = evs[0]["conversationId"]
    assert client.get(f"/api/download/{table['csvFileId']}", headers=LABS).status_code == 200
    other = client.get(f"/api/download/{table['csvFileId']}?customer=acme_labs", headers={**DX, "X-Customer": "acme_labs"})
    assert other.status_code in (403, 404)
    share = client.put(f"/api/conversations/{conv}/share", json={"includeData": True}, headers=LABS)
    assert share.status_code == 200, share.text
    share_id = share.json()["shareId"]
    assert client.get(f"/api/shared/{share_id}", headers=LABS).status_code == 200
    assert client.get(f"/api/shared/{share_id}?customer=acme_labs", headers={**DX, "X-Customer": "acme_labs"}).status_code in (403, 404)


# ------------------------------------------------------------- the real mock path (Cognito -> open_session)

@pytest.fixture(scope="module")
def world(tmp_path_factory):
    from ccsim.edi_mock import Scenario
    from ccsim.world import build_world
    built = build_world(tmp_path_factory.mktemp("bf-world"),
                        Scenario(days=3, volume_scale=0.05, seed=4, burn_in_days=0, p_corrupt_file=1.0,
                                 p_duplicate_835_file=0.0))
    yield built
    built.aws.shutdown()


def test_sim_path_opens_sessions_from_the_token_only(world, monkeypatch):
    import ccsim.query as query
    from analyzer.llm import ScriptedLLM
    from ccsim.query import login
    sql = "SELECT COUNT(DISTINCT claim_key) AS claims FROM sem.claims"
    opened = []
    real = query.open_session

    def spy(aws, dep, token, escalate=False, **kw):
        s = real(aws, dep, token, escalate=escalate, **kw)
        opened.append((s.customer, s.user, escalate))
        return s

    monkeypatch.setattr(query, "open_session", spy)
    script = lambda: ScriptedLLM([  # noqa: E731
        {"tool_calls": [{"name": "run_sql", "arguments": {"sql": sql}}]},
        {"tool_calls": [{"name": "final_answer", "arguments": {"sql": sql, "answer": "Distinct claims."}}]}])
    client = TestClient(create_app(auth=SimAuth(world.aws, world.dep), settings=Settings(heartbeat_seconds=5),
                                   llm_factory=script))
    headers = {"Authorization": "Bearer " + login(world.aws, "ana@xifin.example"), **HINT_HEADERS}
    body = {"messages": [{"id": "u1", "role": "user", "text": "How many claims?"}], **HINT_FIELDS,
            "options": dict(HINT_OPTIONS)}
    r = client.post("/api/chat" + HINT_QUERY, json=body, headers=headers)
    assert r.status_code == 200
    assert events(r)[-1]["finishReason"] == "stop"
    assert opened and all(o == ("acme_labs", "ana@xifin.example", False) for o in opened), opened
