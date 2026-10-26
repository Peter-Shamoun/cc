"""1.16.0 edge-case demo scenarios (CONTRACT.md §9.1): every published edge example and every live demo run is on
contract (schema + invariants + stream_rules.json) and shows the edge it exists for."""
from __future__ import annotations

import json

import pytest

from api.backends import CANCEL_AT, LONG_SQL, SCENARIOS
from api.contract import CONTRACT_VERSION, gen_examples
from api.contract.conformance import check
from api.tests.test_contract import check_invariants

EDGE = gen_examples.EDGE
NEW_IN_116 = ("empty", "multi_table", "long_sql", "many_repairs", "clarify_nested", "escalation_abstain",
              "cancel_plan", "cancel_sql", "cancel_repair", "cancel_candidates", "cancel_escalation", "cancel_render")


def _types(evs, kind):
    return [e for e in evs if e["type"] == kind]


def _parts(evs, kind):
    return [e["part"] for e in evs if e["type"] == "part" and e["part"]["type"] == kind]


@pytest.fixture(scope="module")
def ex():
    return {k: gen_examples.load_edge(k) for k in EDGE}


@pytest.fixture(scope="module")
def live():
    return gen_examples.capture_edge(0.0)


def test_every_new_scenario_is_in_the_schema_and_has_an_example():
    schema = json.loads((gen_examples.EXAMPLES.parent / "v1.schema.json").read_text())
    enum = schema["$defs"]["ChatRequest"]["properties"]["scenario"]["enum"]
    assert enum == list(SCENARIOS) and "report" in enum
    assert set(NEW_IN_116) <= set(SCENARIOS)
    assert {v[1] for v in EDGE.values()} == set(NEW_IN_116) | {"clarify_multi"}  # 1.23.0
    assert set(CANCEL_AT) == {s for s in NEW_IN_116 if s.startswith("cancel_")}
    assert tuple(map(int, CONTRACT_VERSION.split("."))) >= (1, 16, 0)


@pytest.mark.parametrize("name", sorted(EDGE))
def test_edge_example_is_on_contract(name, ex):
    lines = [x for x in (gen_examples.EDGE_DIR / f"{name}.jsonl").read_text().splitlines() if x.strip()]
    evs = ex[name]
    check_invariants(evs)
    assert check(evs) == [], name
    assert evs[-1]["finishReason"] == EDGE[name][2]
    assert all(len(x) < 20_000 for x in lines)
    raw = "\n".join(lines)
    assert not gen_examples._ID.search(raw) and "Bearer" not in raw and "xifin.example" not in raw


@pytest.mark.parametrize("name", sorted(EDGE))
def test_live_demo_matches_the_example(name, ex, live):
    assert check(live[name]) == [], name
    assert live[name][-1]["finishReason"] == EDGE[name][2]
    assert gen_examples.shape(live[name]) == gen_examples.shape(ex[name]), (
        f"edge/{name}.jsonl drifted: run python -m api.contract.gen_examples")


def test_empty_result_is_a_table_with_no_rows_and_says_so(ex):
    evs = ex["empty"]
    res = _types(evs, "step.sql_result")
    assert res and res[-1]["ok"] and res[-1]["rowCount"] == 0
    (table,) = _parts(evs, "table")
    assert table["rows"] == [] and table["columns"]
    assert not _parts(evs, "chart") and not _parts(evs, "error") and not _parts(evs, "abstain")
    assert "".join(e["delta"] for e in _types(evs, "text.delta")).strip()


def test_multi_table_answer_has_two_tables_each_linked_to_its_sql(ex):
    evs = ex["multi_table"]
    tables, codes = _parts(evs, "table"), {c["id"]: c for c in _parts(evs, "code")}
    assert len(tables) == 2 and len(codes) == 2
    assert [t["title"] for t in tables] == ["By payer", "By claim status"]
    assert {t["sqlPartId"] for t in tables} == set(codes) and all(c["role"] == "query" for c in codes.values())
    charts = _parts(evs, "chart")
    assert {c["tablePartId"] for c in charts} <= {t["id"] for t in tables}
    assert len(_types(evs, "step.sql_running")) >= 2


def test_long_sql_is_streamed_whole_and_readable(ex):
    evs = ex["long_sql"]
    assert len(LONG_SQL) > 4_000
    run = max(_types(evs, "step.sql_running"), key=lambda e: len(e["sql"]))
    assert len(run["sql"]) > 4_000 and "maskedLiterals" not in run  # no patient literals to mask
    (code,) = _parts(evs, "code")
    assert len(code["code"]) > 4_000 and code["code"].count("\n") > 40


def test_many_repairs_chain_each_rerun_to_its_failure(ex):
    evs = ex["many_repairs"]
    repairs = _types(evs, "step.repair_attempt")
    assert len(repairs) >= 4
    failed = {e["stepId"] for e in _types(evs, "step.sql_result") if not e["ok"]}
    assert {r["failedStepId"] for r in repairs} <= failed
    attempts = [e["attempt"] for e in _types(evs, "step.sql_running") if e.get("purpose") == "repair"]
    assert attempts == sorted(attempts) and max(attempts) >= 5
    assert evs[-1]["finishReason"] == "stop" and _parts(evs, "table")


def test_nested_clarification_asks_twice_then_answers(ex):
    first, second, third = ex["clarify_nested"], ex["clarify_nested_2"], ex["clarify_nested_3"]
    c1, c2 = _parts(first, "clarification")[0], _parts(second, "clarification")[0]
    assert c1["clarificationId"] != c2["clarificationId"] and c1["question"] != c2["question"]
    assert second[0]["resumeOf"] == {"clarificationId": c1["clarificationId"], "messageId": first[0]["messageId"]}
    assert third[0]["resumeOf"] == {"clarificationId": c2["clarificationId"], "messageId": second[0]["messageId"]}
    assert len({s[0]["conversationId"] for s in (first, second, third)}) == 1
    assert not _parts(third, "clarification") and _parts(third, "table")
    code = _parts(third, "code")[0]["code"]
    assert "charge_amount" in code and "90 DAY" in code  # both answers were applied


def test_escalation_then_abstain(ex):
    evs = ex["escalation_abstain"]
    (esc,) = _types(evs, "escalation")
    assert esc["from"]["model"] != esc["to"]["model"]
    i_esc = evs.index(esc)
    i_abs = next(i for i, e in enumerate(evs) if e["type"] == "part" and e["part"]["type"] == "abstain")
    assert i_esc < i_abs and evs[-1]["finishReason"] == "abstain"
    assert _parts(evs, "abstain")[0]["suggestions"] and not _parts(evs, "error")
    assert len(evs[-1]["usage"]["models"]) == 2


STAGE_SEEN = {
    "cancel_plan": lambda evs: not _types(evs, "step.sql_running"),
    "cancel_sql": lambda evs: len(_types(evs, "step.sql_running")) == 1,
    "cancel_repair": lambda evs: bool(_types(evs, "step.repair_attempt")),
    "cancel_candidates": lambda evs: any(e.get("candidateId") == "k2" for e in _types(evs, "step.sql_running")),
    "cancel_escalation": lambda evs: bool(_types(evs, "escalation")),
    "cancel_render": lambda evs: bool(_types(evs, "text.start")),
}


@pytest.mark.parametrize("name", sorted(STAGE_SEEN))
def test_cancel_at_every_stage_closes_everything(name, ex):
    evs = ex[name]
    assert evs[-1]["finishReason"] == "aborted" and STAGE_SEEN[name](evs)
    results = {e["stepId"]: e for e in _types(evs, "step.sql_result")}
    for run in _types(evs, "step.sql_running"):
        assert run["stepId"] in results, f"{name}: {run['stepId']} left running"
    if name in ("cancel_sql", "cancel_candidates", "cancel_repair"):
        assert any(r.get("error", {}).get("code") == "cancelled" for r in results.values())
    assert not _parts(evs, "error")  # Stop is an outcome, not an error
    starts = {e["blockId"] for e in evs if e["type"].endswith(".start") and "blockId" in e}
    ends = {e["blockId"] for e in evs if e["type"].endswith(".end") and "blockId" in e}
    assert starts <= ends, f"{name}: open block"


def test_cancel_stops_before_answer_parts(ex):
    for name in ("cancel_plan", "cancel_sql", "cancel_candidates", "cancel_render"):
        assert not _parts(ex[name], "table"), name
