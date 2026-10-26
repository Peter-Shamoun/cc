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


def _recording_sleep(monkeypatch):
    """Record every time.sleep(seconds) while still sleeping. Asserting on the requested sleeps, not wall time, keeps
    this deterministic on a loaded box (a sleep can overshoot under load, never undershoot)."""
    real, calls = time.sleep, []

    def sleep(seconds):
        calls.append(seconds)
        real(seconds)
    monkeypatch.setattr(time, "sleep", sleep)
    return calls


def test_paced_answer_streams_over_time(monkeypatch):
    calls = _recording_sleep(monkeypatch)
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0.05,
                                                     stream_delay_seconds=0.002, demo_pace=True)))
    paced, evs = _ask(client, "How many claims?")
    assert evs[-1]["type"] == "message.end"
    deltas = sum(e["type"] == "text.delta" for e in evs)
    assert deltas > 1
    model = sum(c for c in calls if c != 0.002)
    assert model >= 0.045, "model-latency pauses (cancel-aware, sliced into <= 0.05 s sleeps)"
    assert calls.count(0.002) >= 1, "a pause per streamed text chunk"
    # lower bound only: sleeps cannot finish early, however loaded the box is
    assert paced >= sum(calls) * 0.9
    calls.clear()
    client0 = TestClient(create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0,
                                                      stream_delay_seconds=0, demo_pace=False)))
    _ask(client0, "How many claims?")
    assert sum(calls) < 0.01, "no pacing when it is off"


def test_slow_scenario_has_a_floor_so_it_can_be_stopped():
    client = TestClient(create_app(settings=Settings(heartbeat_seconds=5, model_delay_seconds=0,
                                                     stream_delay_seconds=0, demo_pace=True)))
    elapsed, evs = _ask(client, "How many claims? answer slowly")
    assert evs[-1]["type"] == "message.end"
    assert elapsed >= SLOW_FLOOR_SECONDS
