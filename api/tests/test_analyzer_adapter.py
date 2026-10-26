"""Real-analyzer adapter stub (1.12.0): fixture-only replays of analyzer runs → contract events.

No model, network or database is touched: recorded completions are replayed through the same LiveTap →
outcome_parts → emit_parts path as POST /api/chat. Every replay must be schema-valid and pass the shared
conformance checker. The paid-API switch is off by default and is not wired into the app.
"""
import json
import pathlib
import re

import pytest

from analyzer.llm import PaidAPIDisabled
from api.analyzer_adapter import (PAID_SWITCH_ENV, PAID_SWITCH_VALUE, Recording, build_real_llm, events_for_recording,
                                  paid_api_enabled)
from api.contract.conformance import check
from api.tests.test_titles import _validator

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "analyzer_runs"
NAMES = sorted(p.stem for p in FIXTURES.glob("*.json"))
EVENT = _validator("StreamEvent")


def replay(name, **kw):
    return events_for_recording(Recording.from_json(json.loads((FIXTURES / f"{name}.json").read_text())), **kw)


def types(evs):
    return [e["type"] for e in evs]


def parts(evs):
    return [e["part"] for e in evs if e["type"] == "part"]


def test_fixture_set():
    assert NAMES == ["abstain", "answer", "candidates", "clarify", "escalation", "no_answer", "refusal", "repair"]


@pytest.mark.parametrize("name", NAMES)
def test_replay_is_schema_valid_and_conformant(name):
    evs = replay(name)
    for e in evs:
        EVENT.validate(e)
    assert check(evs) == [], name
    assert [e["seq"] for e in evs] == list(range(len(evs)))


@pytest.mark.parametrize("name,finish", [("answer", "stop"), ("repair", "stop"), ("escalation", "stop"),
                                         ("candidates", "stop"), ("clarify", "needs_input"), ("refusal", "abstain"),
                                         ("abstain", "abstain"), ("no_answer", "error")])
def test_finish_reason_mapping(name, finish):
    assert replay(name)[-1]["finishReason"] == finish


def test_answer_maps_tools_to_steps_and_answer_to_parts():
    evs = replay("answer")
    explore = next(e for e in evs if e["type"] == "step.explore")
    assert explore["probe"] == "describe_view" and explore["target"] == "sem.claims" and explore["summary"] == "5 columns"
    runs = [e for e in evs if e["type"] == "step.sql_running"]
    assert [r["label"] for r in runs] == ["Running query", "Final query"]
    res = [e for e in evs if e["type"] == "step.sql_result"]
    assert all(r["ok"] for r in res) and res[0]["rowCount"] == 3 and res[-1]["columns"][0] == {"name": "payer_name"}
    check_ev = next(e for e in evs if e["type"] == "step.check")
    assert check_ev["verdict"] == "pass" and check_ev["targetStepId"] == runs[-1]["stepId"]
    assert types(evs).count("reasoning.delta") == 1 and "reasoning.end" in types(evs)
    kinds = [p["type"] for p in parts(evs)]
    assert kinds[:2] == ["code", "table"] and "callout" in kinds
    table = next(p for p in parts(evs) if p["type"] == "table")
    assert table["rowCount"] == 3 and table["sqlPartId"] == parts(evs)[0]["id"] and table["sensitivity"] == "deidentified"
    start = next(e for e in evs if e["type"] == "table.start")
    assert start["partId"] == table["id"] and start["sqlPartId"] == table["sqlPartId"]
    text = "".join(e["delta"] for e in evs if e["type"] == "text.delta")
    assert text == "Paid amount by payer."
    usage = evs[-1]["usage"]
    assert usage["queries"] == 2 and usage["costIsEstimate"] is True and usage["models"][0]["calls"] == 3


def test_sql_error_maps_to_failed_result_then_repair():
    evs = replay("repair")
    failed = next(e for e in evs if e["type"] == "step.sql_result" and not e["ok"])
    assert failed["error"]["code"] == "binder_error" and "net_paid" not in failed["error"]["message"]  # literals masked
    rep = next(e for e in evs if e["type"] == "step.repair_attempt")
    assert rep["failedStepId"] == failed["stepId"] and rep["attempt"] == 1
    nxt = next(e for e in evs if e["type"] == "step.sql_running" and e.get("repairOf") == rep["stepId"])
    assert nxt["purpose"] == "repair" and nxt["attempt"] == 2


def test_model_change_maps_to_escalation():
    esc = next(e for e in replay("escalation") if e["type"] == "escalation")
    assert esc["from"] == {"model": "claude-haiku-5-5", "tier": "small"}
    assert esc["to"] == {"model": "claude-sonnet-5-5", "tier": "medium"} and esc["trigger"] == "repair_exhausted"


def test_candidates_and_abstain():
    cand = next(e for e in replay("candidates") if e["type"] == "step.candidates")
    assert [c["candidateId"] for c in cand["candidates"]] == ["k1", "k2"] and cand["chosenId"] == "k1"
    assert cand["candidates"][0]["agreesWith"] == ["k2"] and cand["method"] == "execution_agreement"
    evs = replay("abstain")
    cand = next(e for e in evs if e["type"] == "step.candidates")
    assert cand["chosenId"] is None and cand["candidates"][0]["agreesWith"] == []
    assert parts(evs)[0]["type"] == "abstain" and parts(evs)[0]["category"] == "low_confidence"
    assert all(e["type"] != "step.check" for e in evs)


def test_ask_and_refuse():
    clar = parts(replay("clarify"))[0]
    assert clar["type"] == "clarification" and clar["question"] == "Which rate do you mean?"
    assert [c["id"] for c in clar["choices"]] == ["ncr", "den"]
    ref = parts(replay("refusal"))[0]
    assert ref["type"] == "abstain" and ref["category"] == "phi_request"


def test_no_answer_is_a_retryable_error_part():
    p = parts(replay("no_answer"))[-1]
    assert p["type"] == "error" and p["retryable"] is True


@pytest.mark.parametrize("after", [1, 2])
def test_stop_mid_run_aborts_cleanly(after):
    evs = replay("repair", cancel_after_call=after)
    assert evs[-1]["finishReason"] == "aborted"
    assert check(evs) == []
    assert not any(e["type"] in ("part", "text.start") for e in evs)


def test_replay_never_leaks_row_values_into_steps():
    evs = replay("answer")
    steps = json.dumps([e for e in evs if e["type"].startswith("step.")])
    assert "Synthetic Payer" not in steps and "15234" not in steps


# -- the paid-API switch ----------------------------------------------------------------------------------------

class _Fake:
    def __init__(self, allow_paid_api=False):
        self.allow_paid_api = allow_paid_api


FAKES = {"anthropic": _Fake, "bedrock": _Fake}


def test_switch_is_off_by_default(monkeypatch):
    monkeypatch.delenv(PAID_SWITCH_ENV, raising=False)
    assert paid_api_enabled() is False
    for kind in ("anthropic", "bedrock"):
        with pytest.raises(PaidAPIDisabled):
            build_real_llm(kind, factories=FAKES)
        with pytest.raises(PaidAPIDisabled):
            build_real_llm(kind, enable=True, factories=FAKES)


@pytest.mark.parametrize("value", ["1", "true", "yes", PAID_SWITCH_VALUE.upper(), PAID_SWITCH_VALUE + " "])
def test_switch_needs_the_exact_phrase(value):
    env = {PAID_SWITCH_ENV: value}
    assert paid_api_enabled(env) is False
    with pytest.raises(PaidAPIDisabled):
        build_real_llm("anthropic", enable=True, env=env, factories=FAKES)


def test_switch_needs_code_and_env_and_builds_nothing_real():
    env = {PAID_SWITCH_ENV: PAID_SWITCH_VALUE}
    with pytest.raises(PaidAPIDisabled):
        build_real_llm("anthropic", env=env, factories=FAKES)  # env alone is not enough
    llm = build_real_llm("bedrock", enable=True, env=env, factories=FAKES)  # fakes only: nothing is called
    assert isinstance(llm, _Fake) and llm.allow_paid_api is True
    with pytest.raises(ValueError):
        build_real_llm("openai", enable=True, env=env, factories=FAKES)


def test_app_does_not_wire_the_adapter():
    src = (pathlib.Path(__file__).parents[1] / "app.py").read_text()
    assert "build_real_llm" not in src and "analyzer_adapter" not in src and PAID_SWITCH_ENV not in src
    assert not re.search(r"allow_paid_api\s*=\s*True", src)


# -- analyst/* (bench.agents.Answer) normalization ----------------------------------------------------------------

from dataclasses import dataclass, field  # noqa: E402

from api.analyzer_adapter import answer_from_bench  # noqa: E402


@dataclass
class BenchAnswer:  # same fields as bench.agents.Answer; not imported, so this stays a fixture
    sql: str | None = None
    rows: list | None = None
    text: str = ""
    refused: bool = False
    usage: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)


def test_bench_answer_normalization():
    ok = answer_from_bench(BenchAnswer(sql="SELECT 1 AS n", rows=[(1,)], text="One."), columns=["n"])
    assert (ok.kind, ok.columns, ok.rows) == ("answer", ["n"], [[1]])
    assert answer_from_bench(BenchAnswer(rows=[(1, 2)])).columns == ["col_1", "col_2"]
    assert answer_from_bench(BenchAnswer(rows=[(1, 2)]), columns=["only_one"]).columns == ["col_1", "col_2"]
    assert answer_from_bench(BenchAnswer(refused=True, text="No.")).kind == "refusal"
    ab = answer_from_bench(BenchAnswer(text="candidates disagreed", usage={"selection": {"mode": "agree_or_abstain"}}))
    assert ab.kind == "abstain" and ab.selection == {"mode": "agree_or_abstain"}


def test_bench_answer_replays_conformant():
    data = json.loads((FIXTURES / "answer.json").read_text())
    norm = answer_from_bench(BenchAnswer(sql=data["answer"]["sql"], rows=data["answer"]["rows"], text="By payer."),
                             columns=data["answer"]["columns"])
    data["answer"] = {k: getattr(norm, k) for k in ("kind", "text", "sql", "rows", "columns", "model")}
    evs = events_for_recording(Recording.from_json(data))
    assert check(evs) == [] and evs[-1]["finishReason"] == "stop"
