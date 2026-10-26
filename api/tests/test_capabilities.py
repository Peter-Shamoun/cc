"""Contract 1.4.0: GET /api/capabilities (strategies + models for a picker) and options validation."""
import json

import jsonschema
import pytest

from api.contract import CONTRACT_VERSION, gen_examples
from api.tests.conftest import LABS, events
from api.tests.test_titles import _validator


def test_capabilities_shape_and_example(client):
    r = client.get("/api/capabilities")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    body = r.json()
    _validator("Capabilities").validate(body)
    assert body["contractVersion"] == CONTRACT_VERSION and body["demo"] is True
    assert sum(s["default"] for s in body["strategies"]) == 1 and sum(m["default"] for m in body["models"]) == 1
    model_ids = {m["id"] for m in body["models"]}
    assert all(set(s["models"]) <= model_ids for s in body["strategies"])
    assert all("config" not in s for s in body["strategies"]), "analyzer config stays server-side"
    assert json.loads((gen_examples.EXAMPLES / "capabilities.json").read_text()) == body, \
        "examples/capabilities.json drifted: python -m api.contract.gen_examples"


def _chat(client, **options):
    return client.post("/api/chat", json={"messages": [{"role": "user", "text": "Paid amount by payer"}],
                                          "options": options}, headers=LABS)


@pytest.mark.parametrize("strategy", ["tool_loop", "cascade", "candidates"])
def test_known_strategy_is_echoed(client, strategy):
    evs = events(_chat(client, strategy=strategy))
    assert evs[0]["strategy"] == strategy and evs[-1]["type"] == "message.end"


@pytest.mark.parametrize("options,msg", [
    ({"strategy": "yolo"}, "Unknown strategy."),
    ({"model": "gpt-9"}, "Unknown model."),
    ({"strategy": "cascade", "model": "claude-sonnet-5-5"}, "That model isn't available for this strategy."),
])
def test_bad_options_are_422(client, options, msg):
    r = _chat(client, **options)
    assert r.status_code == 422 and r.json()["error"] == {"code": "validation", "message": msg}


def test_chat_request_options_schema():
    v = _validator("ChatRequest")
    v.validate({"messages": [{"role": "user", "text": "x"}], "options": {"strategy": "cascade", "model": "auto"}})
    with pytest.raises(jsonschema.ValidationError):
        v.validate({"messages": [{"role": "user", "text": "x"}], "options": {"strategy": 3}})


# ---- 1.5.0: per-organization availability
from api.tests.conftest import DX  # noqa: E402


def test_signed_in_capabilities_are_per_org(client):
    pub = client.get("/api/capabilities").json()
    assert pub["scope"] == "public" and all(s["available"] for s in pub["strategies"])
    labs = client.get("/api/capabilities", headers=LABS).json()
    assert labs["scope"] == "organization" and all(m["available"] for m in labs["models"])
    dx = client.get("/api/capabilities", headers=DX).json()
    _validator("Capabilities").validate(dx)
    cand = next(s for s in dx["strategies"] if s["id"] == "candidates")
    assert cand["available"] is False and "organization" in cand["unavailableReason"]
    sonnet = next(m for m in dx["models"] if m["id"] == "claude-sonnet-5-5")
    assert sonnet["available"] is False
    assert all(("unavailableReason" in x) == (not x["available"]) for x in dx["strategies"] + dx["models"])


def test_chat_refuses_what_the_org_cannot_use(client):
    r = client.post("/api/chat", json={"messages": [{"role": "user", "text": "Paid amount by payer"}],
                                       "options": {"strategy": "candidates"}}, headers=DX)
    assert r.status_code == 422 and r.json()["error"]["code"] == "validation"
    ok = client.post("/api/chat", json={"messages": [{"role": "user", "text": "Paid amount by payer"}],
                                        "options": {"strategy": "candidates"}}, headers=LABS)
    assert ok.status_code == 200


def test_org_policy_env_override(client, monkeypatch):
    monkeypatch.setenv("CC_API_ORG_POLICY", json.dumps({"acme_labs": {"strategies": {"cascade": "Off."}}}))
    labs = client.get("/api/capabilities", headers=LABS).json()
    assert next(s for s in labs["strategies"] if s["id"] == "cascade")["unavailableReason"] == "Off."


def test_payload_too_large_validates_as_http_error():
    from fastapi.testclient import TestClient
    from api.app import Settings, create_app
    c = TestClient(create_app(settings=Settings(heartbeat_seconds=5, max_body_bytes=1024)))
    r = c.post("/api/chat", content=b'{"messages":[{"role":"user","text":"' + b"x" * 2000 + b'"}]}',
               headers={**LABS, "content-type": "application/json"})
    assert r.status_code == 413
    _validator("HttpError").validate(r.json())
    assert r.json()["error"]["code"] == "payload_too_large" and r.json()["error"]["legacyCode"] == "validation"
