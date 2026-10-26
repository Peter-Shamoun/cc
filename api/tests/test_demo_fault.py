"""Demo-only fault injection for the web client's 429 auto-retry (x-demo-fault: rate_limited)."""
from api.tests.conftest import LABS, ask

BODY = {"messages": [{"id": "u1", "role": "user", "text": "Denial rate by payer"}]}


def test_demo_fault_returns_429_with_retry_after_and_runs_nothing(client):
    r = client.post("/api/chat", json=BODY, headers={**LABS, "x-demo-fault": "rate_limited"})
    assert r.status_code == 429
    assert r.headers["retry-after"] == "3"
    assert r.headers["cache-control"] == "no-store"
    assert r.json()["error"]["code"] == "rate_limited"
    assert client.get("/api/conversations", headers=LABS).json()["items"] == []


def test_without_the_header_the_same_request_streams(client):
    response, evs = ask(client, "Denial rate by payer")
    assert evs[-1]["type"] == "message.end"


def test_unknown_fault_value_is_ignored(client):
    r = client.post("/api/chat", json=BODY, headers={**LABS, "x-demo-fault": "boom"})
    assert r.status_code == 200


def test_fault_needs_auth_first(client):
    r = client.post("/api/chat", json=BODY, headers={"x-demo-fault": "rate_limited"})
    assert r.status_code == 401
