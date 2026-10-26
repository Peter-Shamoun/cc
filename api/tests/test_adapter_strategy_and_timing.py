"""Adapter ``message.start.strategy`` is a capabilities id; latency stages partition ``totalMs`` exactly; the
load-tolerant test budget helper."""
import json
import pathlib

import pytest

from api.analyzer_adapter import Recording, adapter_strategy, events_for_recording
from api.capabilities import DEFAULT_STRATEGY, STRATEGY_IDS
from api.live import STAGES, partition_ms
from api.tests import timing

FIX = pathlib.Path(__file__).with_name("fixtures") / "analyzer_runs"
EXPECTED = {"answer": "tool_loop", "repair": "tool_loop", "clarify": "tool_loop", "no_answer": "tool_loop",
            "refusal": "tool_loop", "phi_reasoning": "tool_loop", "escalation": "cascade",
            "candidates": "candidates", "abstain": "candidates"}


def _rec(name):
    return Recording.from_json(json.loads((FIX / f"{name}.json").read_text()))


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_recorded_runs_announce_a_capabilities_strategy(name):
    start = events_for_recording(_rec(name))[0]
    assert start["type"] == "message.start" and start["strategy"] == EXPECTED[name]
    assert start["strategy"] in STRATEGY_IDS


def test_explicit_strategy_wins_and_must_be_known():
    rec = _rec("answer")
    rec.strategy = "cascade"
    assert events_for_recording(rec)[0]["strategy"] == "cascade"
    assert adapter_strategy("candidates") == "candidates"
    with pytest.raises(ValueError):
        adapter_strategy("analyst")
    assert adapter_strategy() == DEFAULT_STRATEGY
    assert adapter_strategy(multi_candidate=True, escalated=True) == "candidates"


@pytest.mark.parametrize("raw,total", [
    ({"plan": 72.4, "sql": 7.6, "repair": 0, "checks": 1.2, "render": 85.9}, 150),   # tap ran past totalMs
    ({"plan": 10.5}, 400),                                                           # slow start under load
    ({}, 0), ({"sql": 3.3, "render": 3.3, "checks": 3.4}, 10), ({"plan": -1, "sql": 5}, 3)])
def test_partition_is_exact_and_non_negative(raw, total):
    out = partition_ms(raw, total)
    assert list(out) == list(STAGES) and sum(out.values()) == total and min(out.values()) >= 0


def test_partition_keeps_shape_when_drift_is_small():
    raw = {"plan": 100.2, "sql": 50.4, "repair": 0.0, "checks": 10.1, "render": 39.0}
    out = partition_ms(raw, 200)
    assert all(abs(out[k] - raw[k]) <= 1 for k in STAGES)


def test_budget_stretches_with_load_and_scale(monkeypatch):
    monkeypatch.setattr(timing.os, "getloadavg", lambda: (40.0, 0, 0))
    monkeypatch.setattr(timing.os, "cpu_count", lambda: 4)
    assert timing.budget(1.0) == 10.0                      # capped
    monkeypatch.setattr(timing.os, "getloadavg", lambda: (0.5, 0, 0))
    assert timing.budget(2.0) == 2.0                       # never shrinks
    monkeypatch.setenv("CC_TEST_TIME_SCALE", "3")
    assert timing.budget(2.0) == 6.0
    assert timing.wait_until(lambda: True, 0.01) and not timing.wait_until(lambda: False, 0.01)
