"""Contract 1.6.0: per-stage latency breakdown in message.end.usage.latency.stages (CONTRACT.md §7)."""
from fastapi.testclient import TestClient

from api.app import Settings, create_app
from api.live import STAGES
from api.tests.conftest import LABS, events
from api.tests.test_titles import _validator


def _run(question, scenario=None, delay=0.02):
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=delay)))
    body = {"messages": [{"id": "u1", "role": "user", "text": question}]}
    if scenario:
        body["scenario"] = scenario
    response = client.post("/api/chat", json=body, headers=LABS)
    assert response.status_code == 200, response.text
    return events(response)


def test_stages_present_and_sum_to_total():
    evs = _run("What is the denial rate by payer?")
    end = evs[-1]
    assert end["type"] == "message.end" and end["finishReason"] == "stop"
    latency = end["usage"]["latency"]
    stages = latency["stages"]
    assert list(stages) == list(STAGES)
    assert all(isinstance(v, int) and v >= 0 for v in stages.values())
    assert stages["plan"] > 0, "simulated model turns count as planning"
    # every instant belongs to one stage: exactly, under any load (api.live.partition_ms)
    assert sum(stages.values()) == latency["totalMs"]
    _validator("MessageEnd").validate(end)


def test_repair_time_lands_in_repair_stage():
    evs = _run("Show denials", scenario="repair")
    assert any(e["type"] == "step.repair_attempt" for e in evs)
    stages = evs[-1]["usage"]["latency"]["stages"]
    assert stages["repair"] > 0, stages


def test_schema_requires_all_stages():
    v = _validator("LatencyStages")
    v.validate({"plan": 1, "sql": 2, "repair": 0, "checks": 0, "render": 3, "queue": 4})
    assert not v.is_valid({"plan": 1, "sql": 2})
    assert not v.is_valid({"plan": -1, "sql": 0, "repair": 0, "checks": 0, "render": 0})
