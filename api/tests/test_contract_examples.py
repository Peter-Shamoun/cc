"""api/contract/examples/*.jsonl are the canonical example streams (QA e2e + frontend replay them).
Every line validates against the schema, every file obeys the CONTRACT.md §2 invariants and its outcome's
finishReason, and the event-type sequence still matches what the demo backend emits today."""
import json

import pytest

from api.contract import gen_examples
from api.tests.test_contract import check_invariants

FINISH = {"answer": "stop", "tool_steps": "stop", "abstain": "abstain", "clarify": "needs_input",
          "clarify_resume": "stop", "escalation": "stop", "chart": "stop", "candidates": "stop", "error": "error",
          "report": "stop", "regenerate_concise": "stop", "report_v2": "stop"}
REQUIRED_BY_QA = ("answer", "tool_steps", "abstain", "clarify", "escalation")


def _types(evs, kind):
    return [e for e in evs if e["type"] == kind]


def _parts(evs, kind):
    return [e["part"] for e in evs if e["type"] == "part" and e["part"]["type"] == kind]


@pytest.mark.parametrize("outcome", sorted(FINISH))
def test_example_is_on_contract(outcome):
    path = gen_examples.EXAMPLES / f"{outcome}.jsonl"
    lines = [line for line in path.read_text().splitlines() if line.strip()]
    evs = [json.loads(line) for line in lines]
    check_invariants(evs)
    assert evs[-1]["finishReason"] == FINISH[outcome]
    assert all(len(line) < 20_000 for line in lines)


def test_qa_outcomes_published():
    for outcome in REQUIRED_BY_QA:
        assert (gen_examples.EXAMPLES / f"{outcome}.jsonl").exists(), outcome
    assert set(FINISH) == set(gen_examples.OUTCOMES) | {gen_examples.RESUME, gen_examples.REGENERATE,
                                                       gen_examples.REPORT_V2}


def test_examples_cover_the_contract_outcomes():
    ex = {k: gen_examples.load(k) for k in FINISH}
    # answer: text + SQL code part linked from the table + rich usage (cost, tokens, latency)
    table = _parts(ex["answer"], "table")[0]
    code = _parts(ex["answer"], "code")[0]
    assert table["sqlPartId"] == code["id"] and code["role"] == "query"
    usage = ex["answer"][-1]["usage"]
    assert usage["costUsd"] > 0 and usage["costIsEstimate"] is True and usage["tokens"]["input"] > 0
    assert usage["latency"]["totalMs"] > 0 and usage["models"]
    assert set(usage["latency"]["stages"]) == {"plan", "sql", "repair", "checks", "render"}  # 1.6.0
    # tool steps: failed query -> repair attempt -> repaired rerun (attempt 2) -> verifier check
    steps = ex["tool_steps"]
    failed = next(e for e in _types(steps, "step.sql_result") if not e["ok"])
    repair = _types(steps, "step.repair_attempt")[0]
    rerun = next(e for e in _types(steps, "step.sql_running") if e.get("repairOf") == repair["stepId"])
    assert repair["failedStepId"] == failed["stepId"] and rerun["attempt"] == 2 and rerun["purpose"] == "repair"
    assert _types(steps, "step.check") and _types(steps, "step.explore")
    # abstain is an outcome with suggestions, not an error
    ab = _parts(ex["abstain"], "abstain")[0]
    assert ab["suggestions"] and not _parts(ex["abstain"], "error")
    # clarify: choices + free text; the resume example points back at the asking message
    clar = _parts(ex["clarify"], "clarification")[0]
    assert clar["choices"] and clar["allowFreeText"] is True
    resume_of = ex["clarify_resume"][0]["resumeOf"]
    assert resume_of == {"clarificationId": clar["clarificationId"], "messageId": ex["clarify"][0]["messageId"]}
    # escalation: from/to model + reason + trigger, and both models in usage
    esc = _types(ex["escalation"], "escalation")[0]
    assert esc["from"]["model"] != esc["to"]["model"] and esc["reason"] and esc["trigger"]
    assert len(ex["escalation"][-1]["usage"]["models"]) == 2
    assert _parts(ex["chart"], "chart") and _types(ex["candidates"], "step.candidates")
    assert len(_parts(ex["error"], "error")) == 1


def test_examples_are_normalised_and_phi_free():
    for outcome in FINISH:
        raw = (gen_examples.EXAMPLES / f"{outcome}.jsonl").read_text()
        assert not gen_examples._ID.search(raw), "random ids leaked"
        assert "Bearer" not in raw and "xifin.example" not in raw


def test_examples_match_demo_backend():
    captured = gen_examples.capture(0.0)
    for outcome, evs in captured.items():
        assert gen_examples.shape(gen_examples.load(outcome)) == gen_examples.shape(evs), (
            f"{outcome}.jsonl drifted from the demo backend: run python -m api.contract.gen_examples")


def test_report_v2_example_revises_the_report_in_place():
    """1.19.0 (QA-C1): the follow-up keeps each artifact's artifactId and bumps the version."""
    v1 = {p["kind"]: p for p in _parts(gen_examples.load("report"), "artifact")}
    v2 = {p["kind"]: p for p in _parts(gen_examples.load(gen_examples.REPORT_V2), "artifact")}
    assert v1 and set(v1) == set(v2)
    for kind in v1:
        assert (v2[kind]["artifactId"], v2[kind]["version"]) == (v1[kind]["artifactId"], v1[kind]["version"] + 1)
