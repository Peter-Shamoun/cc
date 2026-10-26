"""Demo pacing (QA-P1/P4): scripted demo answers stream like a real model unless CC_API_DEMO_PACE=0."""
import time

from fastapi.testclient import TestClient

from api.app import DEMO_PACE_CHUNK_SECONDS, DEMO_PACE_MODEL_SECONDS, SLOW_FLOOR_SECONDS, Settings, create_app
from api.tests.conftest import LABS, events


def _clear(monkeypatch):
    for name in ("CC_API_DEMO_PACE", "CC_API_MODEL_DELAY_SECONDS", "CC_API_STREAM_DELAY_SECONDS", "CC_API_LLM"):
        monkeypatch.delenv(name, raising=False)


def test_pacing_is_on_by_default_in_demo_mode(monkeypatch):
    _clear(monkeypatch)
    s = Settings()
    assert s.demo_pace is True
    assert s.model_delay_seconds == DEMO_PACE_MODEL_SECONDS
    assert s.stream_delay_seconds == DEMO_PACE_CHUNK_SECONDS


def test_pace_off_switch_and_explicit_values_win(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CC_API_DEMO_PACE", "0")
    s = Settings()
    assert (s.demo_pace, s.model_delay_seconds, s.stream_delay_seconds) == (False, 0.0, 0.0)
    monkeypatch.setenv("CC_API_DEMO_PACE", "1")
    monkeypatch.setenv("CC_API_MODEL_DELAY_SECONDS", "0.5")
    monkeypatch.setenv("CC_API_STREAM_DELAY_SECONDS", "0")
    s = Settings()
    assert (s.model_delay_seconds, s.stream_delay_seconds) == (0.5, 0.0)


def test_no_pacing_for_a_non_scripted_llm(monkeypatch):
    _clear(monkeypatch)
    monkeypatch.setenv("CC_API_LLM", "anthropic")
    s = Settings()
    assert (s.demo_pace, s.model_delay_seconds, s.stream_delay_seconds) == (False, 0.0, 0.0)


def _ask(client, text):
    t0 = time.perf_counter()
    r = client.post("/api/chat", json={"messages": [{"id": "u1", "role": "user", "text": text}]}, headers=LABS)
    assert r.status_code == 200
    return time.perf_counter() - t0, events(r)


def test_paced_answer_streams_over_time():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0.05,
                                                     stream_delay_seconds=0.002, demo_pace=True)))
    paced, evs = _ask(client, "How many claims?")
    assert evs[-1]["type"] == "message.end"
    assert sum(e["type"] == "text.delta" for e in evs) > 1
    client0 = TestClient(create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0,
                                                      stream_delay_seconds=0, demo_pace=False)))
    unpaced, _ = _ask(client0, "How many claims?")
    assert paced > unpaced + 0.04


def test_slow_scenario_has_a_floor_so_it_can_be_stopped():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0,
                                                     stream_delay_seconds=0, demo_pace=True)))
    elapsed, evs = _ask(client, "How many claims? answer slowly")
    assert evs[-1]["type"] == "message.end"
    assert elapsed >= SLOW_FLOOR_SECONDS
