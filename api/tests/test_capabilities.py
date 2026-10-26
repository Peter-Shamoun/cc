"""GET /api/capabilities, and no strategy/model choice (contract 1.24.0: the server runs one analyzer configuration)."""
import json

import pytest
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.contract import CONTRACT_VERSION, gen_examples, schema
from api.tests.conftest import DX, LABS, events
from api.tests.test_titles import _validator

REMOVED_CAPABILITY_KEYS = {"defaults", "strategies", "models", "scope"}


def test_capabilities_shape_and_example(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    _validator("Capabilities").validate(body)
    assert body["contractVersion"] == CONTRACT_VERSION and body["demo"] is True
    assert not REMOVED_CAPABILITY_KEYS & set(body)
    assert json.loads((gen_examples.EXAMPLES / "capabilities.json").read_text()) == body, \
        "examples/capabilities.json drifted: python -m api.contract.gen_examples"


def test_capabilities_do_not_depend_on_the_sign_in(client):
    public = client.get("/api/capabilities").json()
    assert client.get("/api/capabilities", headers=LABS).json() == public
    assert client.get("/api/capabilities", headers=DX).json() == public


def test_schema_has_no_strategy_or_model_choice():
    defs = schema()["$defs"]
    assert not {"StrategyOption", "ModelOption", "CostTier"} & set(defs)
    assert not REMOVED_CAPABILITY_KEYS & set(defs["Capabilities"]["properties"])
    assert "properties" not in defs["ChatRequest"]["properties"]["options"]
    assert "strategy" not in defs["MessageStart"]["properties"]


@pytest.mark.parametrize("options", [{"strategy": "tool_loop"}, {"model": "auto"}, {"strategy": "candidates"},
                                     {"model": "claude-sonnet-5-5"}, {"strategy": ["auto"]}, {"model": None}])
def test_a_strategy_or_model_option_is_422_and_nothing_runs(options):
    app = create_app(settings=Settings(heartbeat_seconds=5))
    r = TestClient(app).post("/api/chat", json={"messages": [{"role": "user", "text": "Paid amount by payer"}],
                                                "options": options}, headers=LABS)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    assert not [e for e in app.state.audit.events if e["event"] == "chat_message"]


def test_message_start_names_no_strategy(client):
    evs = events(client.post("/api/chat", json={"messages": [{"role": "user", "text": "Paid amount by payer"}]},
                             headers=LABS))
    assert evs[0]["type"] == "message.start" and "strategy" not in evs[0] and evs[0]["model"]
    assert evs[-1]["type"] == "message.end"


def test_payload_too_large_validates_as_http_error():
    c = TestClient(create_app(settings=Settings(heartbeat_seconds=5, max_body_bytes=1024)))
    r = c.post("/api/chat", content=b'{"messages":[{"role":"user","text":"' + b"x" * 2000 + b'"}]}',
               headers={**LABS, "content-type": "application/json"})
    assert r.status_code == 413
    _validator("HttpError").validate(r.json())
    assert r.json()["error"]["code"] == "payload_too_large" and r.json()["error"]["legacyCode"] == "validation"
