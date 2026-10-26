"""1.21.0 BACKEND-FIT (CONTRACT §2.1): the user never picks a database, schema, tables or customer. They come from the
signed-in identity, server-side (production: Cognito id token -> ccsim.query.open_session; demo: the demo token and
DEMO_DIRECTORY). No request field, header or query parameter can choose them."""
from __future__ import annotations

import duckdb
import pytest
from fastapi.testclient import TestClient

from api.app import IDENTITY_SCOPED_KEYS, Settings, create_app
from api.backends import DEMO_DIRECTORY, DEMO_LOSSLESS_RELATIONS, AuthError, DemoAuth, demo_connection
from api.contract import schema
from api.tests.conftest import DX, LABS, events, parts

Q = {"messages": [{"role": "user", "text": "Paid amount by payer"}]}
#: response-only fields that happen to use one of the names; none is a choice the client makes
RESPONSE_ONLY = {"TablePart.source"}  # TablePart.source: provenance (views read)


@pytest.fixture
def app():
    return create_app(settings=Settings(heartbeat_seconds=5))


@pytest.fixture
def client(app):
    return TestClient(app)


def test_the_list_is_the_schemas_list():
    assert IDENTITY_SCOPED_KEYS == set(schema()["$defs"]["IdentityScopedKey"]["enum"])
    assert {"customer", "tenant", "database", "schema", "tables", "relations", "dataSource", "escalate"} \
        <= IDENTITY_SCOPED_KEYS


@pytest.mark.parametrize("key", sorted(IDENTITY_SCOPED_KEYS))
def test_a_request_cannot_choose_data_scope_at_the_top_level_or_in_options(app, client, key):
    for body in ({**Q, key: "acme_diagnostics"}, {**Q, "options": {key: ["lossless.claims"]}}):
        r = client.post("/api/chat", json=body, headers=LABS)
        assert r.status_code == 422 and r.json()["error"]["code"] == "validation", body
        assert "sign-in" in r.json()["error"]["message"]
    assert not [e for e in app.state.audit.events if e["event"] == "chat_message"]  # nothing ran


def test_other_unknown_keys_are_still_ignored(client):
    r = client.post("/api/chat", json={**Q, "clientHint": 1, "options": {"legacyToolParts": False, "x": 1}},
                    headers=LABS)
    assert r.status_code == 200 and events(r)[-1]["finishReason"] == "stop"


def test_no_request_shape_in_the_schema_names_a_data_scope():
    found = set()

    def walk(node, path):
        if isinstance(node, dict):
            for name, sub in node.get("properties", {}).items():
                if name in IDENTITY_SCOPED_KEYS:
                    found.add(f"{path}.{name}")
                walk(sub, f"{path}.{name}")
            for k in ("items", "additionalProperties"):
                walk(node.get(k), path)
            for k in ("oneOf", "anyOf", "allOf"):
                for sub in node.get(k, []):
                    walk(sub, path)

    for name, d in schema()["$defs"].items():
        walk(d, name)
    assert found == RESPONSE_ONLY


def _min_key_analyzer():
    from api.tests.conftest import cc, scripted_analyzer
    sql = "SELECT MIN(claim_key) AS first_claim FROM sem.claims"
    return scripted_analyzer(cc("sql", sql), cc("answer", "--sql", sql, "--text", "First claim."))


def _first_claim(client, headers, path="/api/chat"):
    r = client.post(path, json=Q, headers=headers)
    table = next(p for p in parts(events(r)) if p["type"] == "table")
    return table["rows"][0][0]


def test_the_customer_comes_from_the_token_not_headers_or_query():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5), analyzer=_min_key_analyzer()))
    assert _first_claim(client, LABS).startswith("AL")
    assert _first_claim(client, DX).startswith("AD")
    spoof = {**LABS, "X-Customer": "acme_diagnostics", "X-Tenant": "acme_diagnostics", "X-Org": "acme_diagnostics"}
    assert _first_claim(client, spoof).startswith("AL")
    assert _first_claim(client, LABS, "/api/chat?customer=acme_diagnostics&schema=lossless").startswith("AL")


def test_demo_identity_mirrors_the_id_token_and_manifest():
    auth = DemoAuth()
    ident = auth.verify("demo:acme_labs:ana@xifin.example")
    assert (ident.customer, ident.user, ident.relations) == ("acme_labs", "ana@xifin.example", DEMO_DIRECTORY["acme_labs"])
    ctx = auth.authenticate("demo:acme_labs:ana@xifin.example", "r1")
    handle = ctx.opener.open(False)
    assert (handle.customer, handle.user) == ("acme_labs", "ana@xifin.example")
    tables = handle.con.execute("SELECT schema_name || '.' || table_name FROM duckdb_tables()").fetchall()
    assert sorted(t for (t,) in tables) == sorted(ident.relations)  # exactly the identity's relations
    escalated = ctx.opener.open(True)  # the analyzer's audited escalation adds lossless.*, same customer and user
    assert (escalated.customer, escalated.user) == ("acme_labs", "ana@xifin.example")
    tables = escalated.con.execute("SELECT schema_name || '.' || table_name FROM duckdb_tables()").fetchall()
    assert sorted(t for (t,) in tables) == sorted(ident.relations + DEMO_LOSSLESS_RELATIONS)
    for bad in ("", "demo:other_co:ana@x.example", "demo:acme_labs", "acme_labs:ana@x.example"):
        with pytest.raises(AuthError):
            auth.verify(bad)


def test_the_demo_session_cannot_reach_files_or_reconfigure(tmp_path):
    secret = tmp_path / "other_customer.csv"
    secret.write_text("claim_key\nAD100000\n")
    con = demo_connection("acme_labs").cursor()
    with pytest.raises(duckdb.Error):
        con.execute(f"SELECT * FROM read_csv('{secret}')").fetchall()
    with pytest.raises(duckdb.Error):
        con.execute("SET enable_external_access = true")
    with pytest.raises(duckdb.Error):
        con.execute(f"ATTACH '{tmp_path / 'x.db'}' AS x")


def test_relations_must_exist_in_the_demo_manifest():
    with pytest.raises(ValueError):
        demo_connection("acme_labs", relations=("lossless.claims",))
    with pytest.raises(ValueError):
        demo_connection("acme_labs", relations=())
