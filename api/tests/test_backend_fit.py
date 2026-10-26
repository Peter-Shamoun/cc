"""BACKEND-FIT (Peter, 11:57 PM): the user never picks a database, lake, schema or view.

Production opens every session from the Cognito token alone (ccsim.query.open_session): the customer is the
``custom:customer`` claim, the user is ``email`` (or ``sub``), and the relations are fixed server-side (``sem.*``;
``lossless.*`` only when the analyzer itself escalates with a stated reason). These tests pin that nothing a client sends (body
options, extra body fields, query parameters, headers) can choose the customer, the relations or escalation, in the
demo stub and on the real mock path, and that the demo stub derives identity the same way production does.
"""
import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.backends import AuthError, DemoAuth, SimAuth
from api.tests.conftest import DX, LABS, events, parts

#: 1.21.0 (CONTRACT §2.1): a body or options key from IdentityScopedKey is refused with 422 and nothing runs.
#: Data-flavoured keys outside that enum are ignored like any unknown key, and headers and the query string are never
#: read for scope. Either way none of it may change the customer, the relations or escalation.
IGNORED_OPTIONS = {"lossless": True, "views": ["lossless.claims"], "sources": ["raw.x12_837"], "lakehouse": "raw",
                   "allowedDirectories": ["s3://raw/"], "sql": "SELECT * FROM lossless.claims"}
IGNORED_FIELDS = {"views": ["lossless.claims"], "lossless": True, "principal": "acme_diagnostics"}
HINT_QUERY = "?customer=acme_diagnostics&tenant=acme_diagnostics&escalate=1&schema=lossless&scope=raw"
HINT_HEADERS = {"X-Customer": "acme_diagnostics", "X-Tenant": "acme_diagnostics", "X-Scope": "lossless.*",
                "X-Escalate": "1", "X-Database": "raw", "X-Forwarded-User": "dev@xifin.example"}


def _claims_table(client, headers, *, hints: bool):
    body = {"messages": [{"id": "u1", "role": "user", "text": "List all claims"}]}
    if hints:
        body = {**body, **IGNORED_FIELDS, "options": dict(IGNORED_OPTIONS)}
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


def test_demo_session_exposes_only_sem_until_escalated_for_the_same_customer():
    ctx = DemoAuth().authenticate("demo:acme_labs:ana@xifin.example", "r1")
    handle = ctx.opener.open(False)
    rows = handle.con.execute("SELECT DISTINCT table_schema FROM information_schema.tables").fetchall()
    assert {r[0] for r in rows} == {"sem"}
    escalated = ctx.opener.open(True)
    rows = escalated.con.execute("SELECT DISTINCT table_schema FROM information_schema.tables").fetchall()
    assert {r[0] for r in rows} == {"sem", "lossless"}
    assert (escalated.customer, escalated.user) == (handle.customer, handle.user) == ("acme_labs", "ana@xifin.example")
    keys = {r[0][:2] for r in escalated.con.execute("SELECT claim_key FROM sem.claims").fetchall()}
    assert keys == {"AL"}
    # the non-escalated connection still has no lossless schema
    assert {r[0] for r in ctx.opener.open(False).con.execute(
        "SELECT DISTINCT table_schema FROM information_schema.tables").fetchall()} == {"sem"}


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
    assert not [e for e in evs if e.get("probe") == "request_escalation"]   # a hint never escalates


def test_identity_scoped_keys_are_refused_before_anything_runs():
    """Every IdentityScopedKey, in the body or in options, is 422 validation and opens no session."""
    from api.app import IDENTITY_SCOPED_KEYS
    assert {"customer", "tenant", "schema", "database", "relations", "scope", "escalate", "dataSource"} <= IDENTITY_SCOPED_KEYS
    opened = []

    class CountingAuth(DemoAuth):
        def authenticate(self, token, request_id):
            ctx = super().authenticate(token, request_id)
            real = ctx.opener.open
            ctx.opener.open = lambda escalate: (opened.append(escalate), real(escalate))[1]
            return ctx

    client = TestClient(create_app(auth=CountingAuth(), settings=Settings(heartbeat_seconds=5)))
    msg = {"messages": [{"id": "u1", "role": "user", "text": "List all claims"}]}
    for key in sorted(IDENTITY_SCOPED_KEYS):
        for body in ({**msg, key: "acme_diagnostics"}, {**msg, "options": {key: "acme_diagnostics"}}):
            r = client.post("/api/chat", json=body, headers=LABS)
            assert r.status_code == 422, (key, r.status_code)
            assert r.json()["error"]["code"] == "validation"
            assert "sign-in" in r.json()["error"]["message"]
    assert opened == []  # no session was opened (a valid request opens one: CountingAuth records [False])


def test_downloads_stay_inside_the_tokens_customer():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5)))
    _, table = _claims_table(client, LABS, hints=False)
    assert client.get(f"/api/download/{table['csvFileId']}", headers=LABS).status_code == 200
    other = client.get(f"/api/download/{table['csvFileId']}?customer=acme_labs", headers={**DX, "X-Customer": "acme_labs"})
    assert other.status_code in (403, 404)


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
    body = {"messages": [{"id": "u1", "role": "user", "text": "How many claims?"}], **IGNORED_FIELDS,
            "options": dict(IGNORED_OPTIONS)}
    r = client.post("/api/chat" + HINT_QUERY, json=body, headers=headers)
    assert r.status_code == 200
    assert events(r)[-1]["finishReason"] == "stop"
    assert opened and all(o == ("acme_labs", "ana@xifin.example", False) for o in opened), opened
