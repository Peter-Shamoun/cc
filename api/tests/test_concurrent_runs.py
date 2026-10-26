"""Per-user cap on unfinished chat runs (429 rate_limited, which the web client already auto-retries)."""
from fastapi.testclient import TestClient

from api.app import Settings, _Run, create_app
from api.tests.conftest import DX, LABS, ask


def _setup(limit=2):
    app = create_app(settings=Settings(heartbeat_seconds=5, max_concurrent_runs=limit))
    principal = app.state.auth.authenticate(LABS["Authorization"].split(" ", 1)[1], "t").principal
    return app, TestClient(app), principal


def test_cap_returns_429_rate_limited_with_retry_after():
    app, client, principal = _setup()
    for i in range(2):
        app.state.runs[f"m_busy{i}"] = _Run(f"m_busy{i}", principal)
    r = client.post("/api/chat", json={"messages": [{"id": "u1", "role": "user", "text": "How many claims?"}]},
                    headers=LABS)
    assert r.status_code == 429
    assert r.json()["error"]["code"] == "rate_limited"
    assert r.headers["retry-after"] == "3" and r.headers["cache-control"] == "no-store"
    assert any(e["event"] == "chat_rate_limited" and e["reason"] == "concurrent_runs" for e in app.state.audit.events)
    assert not any("How many" in str(e) for e in app.state.audit.events)  # no question text in audit


def test_other_users_are_not_affected_and_finished_runs_do_not_count():
    app, client, principal = _setup()
    for i in range(2):
        app.state.runs[f"m_busy{i}"] = _Run(f"m_busy{i}", principal)
    ask(client, "How many claims?", headers=DX)  # another customer/user: fine
    app.state.runs["m_busy0"].done = True
    _, evs = ask(client, "How many claims?")
    assert evs[-1]["type"] == "message.end"
