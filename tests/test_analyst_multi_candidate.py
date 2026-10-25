"""Result clustering and selection for the multi-candidate analyst. No API, no simulator."""
from datetime import date
from decimal import Decimal

import duckdb
import pytest

from bench.guard import guard
from analyst.variants.multi_candidate.agent import MultiCandidateAgent
from analyst.variants.multi_candidate.cost import (
    TokenSketch,
    formula_latency_s,
    lake_query_latency,
    llm_cost_usd,
)
from analyst.variants.multi_candidate.equivalence import (
    cluster_indices,
    cluster_spec_for_question,
    results_equivalent,
)
from analyst.variants.multi_candidate.llm import LLMResponse, ScriptedLLM, final_answer, refuse, run_sql
from analyst.variants.multi_candidate.prompts import (
    STRATEGY_ORDER,
    STRATEGY_PROMPTS,
    SYSTEM_PREFIX,
    TOOL_SPEC,
    subagent_prompt,
)
from analyst.variants.multi_candidate.screen import SCREEN_ARMS
from analyst.variants.multi_candidate.selector import (
    Candidate,
    LLMSelector,
    LLMVerifier,
    build_clusters,
    parse_selector_response,
    parse_verifier_response,
    select,
)


def test_order_rounding_labels_and_percent():
    spec = {"tol": 0.005}
    assert results_equivalent([(1,), (2,)], [(2,), (1,)], spec)
    assert not results_equivalent([(1,), (2,)], [(2,), (1,)], {"order": "strict", "tol": 0.005})
    assert results_equivalent([(1.0,)], [(1.004,)], spec)
    assert not results_equivalent([(1.0,)], [(1.02,)], spec)
    assert results_equivalent([(Decimal("1.00"),)], [(1.004,)], spec)
    assert results_equivalent([("A", 0.5), ("B", 0.2)], [(0.2, "b"), (0.5, "a")], spec)
    assert results_equivalent([("acme", 0.2)], [("Acme Payer", "acme", 0.2)], spec)
    assert not results_equivalent([(1, 2)], [(2, 1)], spec)
    assert results_equivalent([(10, 0.5)], [(0.5, 10)], spec)
    assert results_equivalent([(date(2026, 7, 1), 5.0)], [("2026-07", 5)], spec)
    assert results_equivalent([], [], spec)
    assert not results_equivalent([], [(1,)], spec)
    assert results_equivalent([(0.25,)], [(25.0,)], {"tol": 0.005, "allow_percent": True})
    assert not results_equivalent([(0.25,)], [(25.0,)], {"tol": 0.005, "allow_percent": False})
    # A count of 1 is a whole number, so percent scaling must not turn it into 100.
    assert not results_equivalent([(1,)], [(100,)], {"tol": 0.005, "allow_percent": True})
    assert results_equivalent([(-12.5,)], [(12.5,)], {"tol": 0.01, "allow_sign_flip": True})
    assert not results_equivalent([(-12.5,)], [(12.5,)], {"tol": 0.01})


def test_topn_ties_cluster_and_a_dropped_better_row_does_not():
    spec = {"order": "topn", "n": 5, "metric_col": 1, "tol": 0.005}
    kept = [("a", 10)] + [(f"p{i}", 5) for i in range(4)]
    other_tie = [("a", 10)] + [(f"p{i}", 5) for i in range(8, 12)]
    assert results_equivalent(kept, other_tie, spec)
    assert results_equivalent(kept, list(reversed(kept)), spec)
    missing_the_leader = [(f"p{i}", 5) for i in range(5)]
    assert not results_equivalent(kept, missing_the_leader, spec)
    # Rounding on the cutoff still counts as the same tie.
    assert results_equivalent(
        [("A", 10.0), ("B", 9.0), ("C", 8.0)],
        [("A", 10.0), ("B", 9.0), ("D", 8.004)],
        {"order": "topn", "n": 3, "metric_col": 1, "tol": 0.005},
    )
    assert not results_equivalent(
        [("A", 10.0), ("B", 9.0), ("C", 8.0)],
        [("A", 10.0), ("B", 9.0), ("E", 7.0)],
        {"order": "topn", "n": 3, "metric_col": 1, "tol": 0.005},
    )


def test_tolerance_bridge_is_one_cluster():
    # 0 ~ 0.004 and 0.004 ~ 0.008, but 0 !~ 0.008. The vote must not split across the bridge.
    candidates = [
        Candidate("direct", sql="a", rows=[(0.0,)]),
        Candidate("grain_and_date", sql="b", rows=[(0.004,)]),
        Candidate("filter_then_aggregate", sql="c", rows=[(0.008,)]),
    ]
    groups = cluster_indices(candidates, {"tol": 0.005})
    assert len(groups) == 1
    assert sorted(groups[0]) == [0, 1, 2]


def test_errors_are_not_a_result_cluster():
    candidates = [
        Candidate("direct", sql="a", rows=[(1,)]),
        Candidate("grain_and_date", sql="b", error="no such column"),
        Candidate("filter_then_aggregate", sql="c", error="no such column"),
    ]
    groups = cluster_indices(candidates, {})
    assert groups == [[0]]


def test_majority_keeps_the_wider_of_two_equivalent_results():
    # An extra text label is judge-acceptable. Submitting the narrower row would drop it.
    candidates = [
        Candidate("direct", sql="SELECT wide", rows=[("label", 2)]),
        Candidate("grain_and_date", sql="SELECT narrow", rows=[(2,)]),
        Candidate("filter_then_aggregate", sql="SELECT wrong", rows=[(3,)]),
    ]
    decision = select(
        candidates, question="How many claims?", spec={"tol": 0.005},
        strategy_order=("direct", "grain_and_date", "filter_then_aggregate"),
        mode="majority_then_pairwise",
    )
    assert not decision.abstained and not decision.selector_called
    assert decision.rows == [("label", 2)]
    assert decision.chosen_strategy == "direct"
    assert decision.cluster_sizes == [2, 1]


def test_majority_keeps_a_supporting_count_beside_an_amount():
    # The judge clusters (count, amount) with (amount,) because the extra column is a whole-number
    # supporting count. The one-column SQL is a different answer when the question asked for both.
    candidates = [
        Candidate("direct", sql="SELECT count, amount", rows=[(5, 65.47)]),
        Candidate("grain_and_date", sql="SELECT amount only", rows=[(65.47,)]),
        Candidate("filter_then_aggregate", sql="SELECT wrong count", rows=[(35,)]),
    ]
    decision = select(
        candidates, question="How many claims and how much was paid?", spec={"tol": 0.01},
        strategy_order=("direct", "grain_and_date", "filter_then_aggregate"),
        mode="majority_then_pairwise",
    )
    assert not decision.abstained and not decision.selector_called
    assert decision.rows == [(5, 65.47)]
    assert decision.chosen_strategy == "direct"
    assert decision.cluster_sizes == [2, 1]


def test_no_majority_calls_the_selector_and_a_decline_abstains():
    candidates = [
        Candidate("direct", sql="a", rows=[(1,)]),
        Candidate("grain_and_date", sql="b", rows=[(2,)]),
        Candidate("filter_then_aggregate", sql="c", rows=[(3,)]),
    ]
    order = ("direct", "grain_and_date", "filter_then_aggregate")

    class Decline:
        def __init__(self):
            self.calls = 0

        def choose(self, question, clusters):
            self.calls += 1
            return None

    decliner = Decline()
    decision = select(
        candidates, question="q", spec={}, strategy_order=order,
        mode="majority_then_pairwise", pairwise=decliner,
    )
    assert decision.abstained and decision.selector_called and decliner.calls == 1
    assert decision.reason == "selector declined"

    class Pick:
        def choose(self, question, clusters):
            assert "SQL" in str(clusters[0].representative.sql) or clusters[0].representative.sql
            return "c2"

    decision = select(
        candidates, question="q", spec={}, strategy_order=order,
        mode="majority_then_pairwise", pairwise=Pick(),
    )
    assert decision.rows == [(3,)]
    assert decision.chosen_strategy == "filter_then_aggregate"


def test_pairwise_always_can_override_a_majority():
    candidates = [
        Candidate("direct", sql="a", rows=[(1,)]),
        Candidate("grain_and_date", sql="b", rows=[(1,)]),
        Candidate("filter_then_aggregate", sql="c", rows=[(9,)]),
    ]

    class PickMinority:
        def __init__(self):
            self.calls = 0

        def choose(self, question, clusters):
            self.calls += 1
            return next(cluster.cluster_id for cluster in clusters if cluster.size == 1)

    picker = PickMinority()
    decision = select(
        candidates, question="q", spec={}, strategy_order=STRATEGY_ORDER,
        mode="pairwise", pairwise=picker,
    )
    assert picker.calls == 1
    assert decision.rows == [(9,)]


def test_agree_or_abstain_and_verifier():
    order = STRATEGY_ORDER[:3]
    agreed = [
        Candidate("direct", sql="a", rows=[(4,)]),
        Candidate("grain_and_date", sql="b", rows=[(4,)]),
        Candidate("filter_then_aggregate", sql="c", rows=[(4,)]),
    ]
    decision = select(agreed, question="q", spec={}, strategy_order=order, mode="agree_or_abstain")
    assert decision.rows == [(4,)]

    split = [
        Candidate("direct", sql="a", rows=[(4,)]),
        Candidate("grain_and_date", sql="b", rows=[(4,)]),
        Candidate("filter_then_aggregate", sql="c", rows=[(5,)]),
    ]
    decision = select(split, question="q", spec={}, strategy_order=order, mode="agree_or_abstain")
    assert decision.abstained

    class Reject:
        def accept(self, question, cluster):
            return False

    decision = select(
        agreed, question="q", spec={}, strategy_order=order, mode="verifier", verifier=Reject(),
    )
    assert decision.abstained and decision.verifier_called and not decision.selector_called

    class Accept:
        def accept(self, question, cluster):
            return True

    decision = select(
        agreed, question="q", spec={}, strategy_order=order, mode="verifier", verifier=Accept(),
    )
    assert decision.rows == [(4,)] and decision.verifier_called


def test_any_veto_refusal_and_a_lone_success_after_errors():
    order = STRATEGY_ORDER[:3]
    mixed = [
        Candidate("direct", sql="a", rows=[(1,)]),
        Candidate("grain_and_date", sql="b", rows=[(1,)]),
        Candidate("filter_then_aggregate", refused=True, text="PHI"),
    ]
    decision = select(
        mixed, question="q", spec={}, strategy_order=order, mode="majority", refusal_policy="any_veto",
    )
    assert decision.refused and decision.text == "PHI"

    lone = [
        Candidate("direct", sql="SELECT 1", rows=[(7,)]),
        Candidate("grain_and_date", sql="bad", error="boom"),
        Candidate("filter_then_aggregate", sql="bad", error="boom"),
    ]
    decision = select(lone, question="q", spec={}, strategy_order=order, mode="majority")
    assert decision.rows == [(7,)]
    decision = select(
        lone, question="q", spec={}, strategy_order=order, mode="majority", min_successes=2,
    )
    assert decision.abstained


def test_question_text_spec_does_not_need_an_answer_key():
    rate = cluster_spec_for_question(
        "What is our overall denial rate? Count each claim once per payer, using its current adjudication."
    )
    assert rate["allow_percent"] is True and rate.get("order") != "topn"
    ranking = cluster_spec_for_question(
        "Which 5 denial reason codes (CARCs) appear on the most denied claims?"
    )
    assert ranking["order"] == "topn" and ranking["n"] == 5
    money = cluster_spec_for_question("What is the total amount we billed, counting each claim once?")
    assert money["tol"] == 0.01 and money["allow_percent"] is False
    signed = cluster_spec_for_question("Give a positive dollar amount of interest.")
    assert signed["allow_sign_flip"] is True


def test_selector_parser_and_prompt_round_trip():
    assert parse_selector_response("I think CLUSTER c1 is right", {"c0", "c1"}) == "c1"
    assert parse_selector_response("CLUSTER c0\nABSTAIN", {"c0"}) is None
    assert parse_selector_response("no idea", {"c0"}) is None
    assert parse_verifier_response("probably ACCEPT") is True
    assert parse_verifier_response("REJECT\nACCEPT") is True
    assert parse_verifier_response("ACCEPT\nREJECT") is False

    clusters = build_clusters(
        [
            Candidate("direct", sql="SELECT 1", rows=[(1,)], columns=["n"], text="one"),
            Candidate("grain_and_date", sql="SELECT 2", rows=[(2,)], columns=["n"], text="two"),
        ],
        {},
        STRATEGY_ORDER,
    )
    llm = ScriptedLLM({"__selector__": [LLMResponse(text="CLUSTER c1", usage={"input_tokens": 15, "output_tokens": 3})]})
    choice = LLMSelector(llm, "claude-sonnet-5-5").choose("How many?", clusters)
    assert choice == "c1"
    sent = llm.calls[0]
    assert sent["strategy"] == "__selector__"
    # The prompt the model saw is not stored on the call log; rebuild it and check the contract.
    from analyst.variants.multi_candidate.prompts import selector_user_message
    prompt = selector_user_message("How many?", clusters)
    assert "SELECT 1" in prompt and "SELECT 2" in prompt and "ABSTAIN" in prompt

    verifier = LLMVerifier(ScriptedLLM({"__verifier__": [LLMResponse(text="REJECT")]}), "claude-sonnet-5-5")
    assert verifier.accept("How many?", clusters[0]) is False


def test_prompts_are_distinct_and_do_not_carry_a_pitfalls_list():
    bodies = list(STRATEGY_PROMPTS.values())
    assert len(bodies) == 5
    assert len(set(bodies)) == 5
    blob = SYSTEM_PREFIX + "\n".join(bodies) + subagent_prompt("direct", "How many claims?", "python3 cli")
    assert "Invariants and common traps" not in blob
    assert "is_latest_submission picks the secondary" not in blob
    assert "final_answer" in TOOL_SPEC[1]["name"] or TOOL_SPEC[1]["name"] == "final_answer"
    briefing = subagent_prompt("grain_and_date", "Show paid by month.", "python3 cli")
    assert "time basis" in briefing and "Show paid by month." in briefing
    assert "truth.json" not in briefing


def test_screen_arm_is_three_prompts_then_majority_or_selector():
    arm = SCREEN_ARMS["mc_n3_majority_then_pairwise"]
    assert arm["n_candidates"] == 3
    assert arm["strategies"] == ["direct", "grain_and_date", "filter_then_aggregate"]
    assert arm["selection"] == "majority_then_pairwise"
    assert arm["selector_model"] == "claude-sonnet-5-5"
    assert arm["temperature"] is None
    assert arm["repair_on_error"] == 0
    assert arm["glossary"] == "basic"
    assert SCREEN_ARMS["mc_n5_majority_then_pairwise"]["n_candidates"] == 5
    assert SCREEN_ARMS["mc_n1_direct"]["strategies"] == ["direct"]


def test_lake_latency_and_token_cost_for_n3_and_n5():
    serial3 = lake_query_latency(3, parallel=False)
    parallel3 = lake_query_latency(3, parallel=True)
    serial5 = lake_query_latency(5, parallel=False)
    parallel5 = lake_query_latency(5, parallel=True)
    assert serial3["latency_s"] == pytest.approx(0.51)
    assert serial3["requests"] == 150
    assert parallel3["latency_s"] == pytest.approx(0.17)
    assert parallel3["requests"] == 150
    assert serial5["latency_s"] == pytest.approx(0.85)
    assert serial5["requests"] == 250
    assert parallel5["latency_s"] == pytest.approx(0.17)
    assert parallel5["mb_fetched"] == pytest.approx(16.7)
    # Median requests and median bytes plugged into the formula are not the reported median latency.
    assert formula_latency_s(50, 3.34) == pytest.approx(0.18965)
    assert formula_latency_s(50, 3.34) != pytest.approx(0.17)

    sonnet = llm_cost_usd(3, "claude-sonnet-5-5", TokenSketch(), warm_cache=True, selector=True)
    assert sonnet["prefix_usd"] == pytest.approx(0.0116)
    assert sonnet["uncached_input_usd"] == pytest.approx(0.003)
    assert sonnet["output_usd"] == pytest.approx(0.024)
    assert sonnet["selector_usd"] == pytest.approx(0.005)
    assert sonnet["total_usd"] == pytest.approx(0.0436)
    assert sonnet["output_price_assumed"] is False

    haiku = llm_cost_usd(3, "claude-haiku-5-5", TokenSketch(), warm_cache=True, selector=True)
    assert haiku["output_price_assumed"] is True
    assert haiku["total_usd"] == pytest.approx(0.00693)
    cold = llm_cost_usd(3, "claude-sonnet-5-5", TokenSketch(), warm_cache=False, selector=False)
    warm = llm_cost_usd(3, "claude-sonnet-5-5", TokenSketch(), warm_cache=True, selector=False)
    assert cold["total_usd"] > warm["total_usd"]


class _MemTools:
    def __init__(self):
        self.con = duckdb.connect()
        self.con.execute("CREATE SCHEMA sem")
        self.con.execute(
            "CREATE TABLE sem.claims (claim_key VARCHAR, charge_amount DOUBLE)"
        )
        self.con.execute("INSERT INTO sem.claims VALUES ('a', 10), ('a', 12), ('b', 5)")
        self.calls = []
        self.describes = 0

    def describe(self) -> str:
        self.describes += 1
        return "sem.claims(claim_key VARCHAR, charge_amount DOUBLE)"

    def sql(self, query: str, _final: bool = False):
        safe = guard(query, 1000, False)
        cursor = self.con.execute(safe)
        columns = [d[0] for d in cursor.description]
        rows = cursor.fetchall()
        self.calls.append(query)
        return columns, rows


def test_agent_majority_executes_through_the_guard_and_skips_the_selector():
    tools = _MemTools()
    usage = {"input_tokens": 11, "output_tokens": 7}
    llm = ScriptedLLM({
        "direct": [
            run_sql("SELECT count(*) AS submissions FROM sem.claims", usage=usage),
            final_answer("SELECT count(DISTINCT claim_key) AS claims FROM sem.claims", "two claims", usage=usage),
        ],
        "grain_and_date": [final_answer(
            "SELECT count(*) AS claims FROM (SELECT DISTINCT claim_key FROM sem.claims) s", "two", usage=usage,
        )],
        "filter_then_aggregate": [final_answer("SELECT count(*) AS submissions FROM sem.claims", "three", usage=usage)],
    })
    agent = MultiCandidateAgent(llm, n_candidates=3, selection="majority_then_pairwise", generator_model="scripted")
    answer = agent.answer("How many distinct claims have we submitted?", tools)
    assert answer.rows == [(2,)]
    assert "DISTINCT" in answer.sql.upper()
    assert answer.usage["input_tokens"] == 44  # 4 completes × 11; the selector was not called
    assert answer.usage["selection"]["selector_called"] is False
    assert answer.usage["selection"]["cluster_sizes"] == [2, 1]
    assert tools.describes == 1
    assert len(tools.calls) == 4  # one explore + three finals


def test_agent_bad_sql_drops_out_and_a_refusal_vetoes():
    tools = _MemTools()
    llm = ScriptedLLM({
        "direct": [final_answer("SELECT count(DISTINCT claim_key) FROM sem.claims", "two")],
        "grain_and_date": [final_answer("SELECT count(DISTINCT patient_control_number) FROM sem.claims", "bad")],
        "filter_then_aggregate": [final_answer("SELECT count(DISTINCT claim_key) FROM sem.claims", "two")],
    })
    answer = MultiCandidateAgent(llm, selection="majority").answer("How many distinct claims?", tools)
    assert answer.rows == [(2,)]
    assert answer.usage["selection"]["n_errors"] == 1

    llm = ScriptedLLM({
        "direct": [final_answer("SELECT count(DISTINCT claim_key) FROM sem.claims", "two")],
        "grain_and_date": [final_answer("SELECT count(DISTINCT claim_key) FROM sem.claims", "two")],
        "filter_then_aggregate": [refuse("That would expose a member id.")],
    })
    answer = MultiCandidateAgent(llm, selection="majority", refusal_policy="any_veto").answer(
        "Give me the member ids.", tools,
    )
    assert answer.refused
    assert "member id" in answer.text


def test_agent_split_uses_the_scripted_selector():
    tools = _MemTools()
    llm = ScriptedLLM({
        "direct": [final_answer("SELECT count(DISTINCT claim_key) FROM sem.claims", "two")],
        "grain_and_date": [final_answer("SELECT sum(charge_amount) FROM sem.claims", "27")],
        "filter_then_aggregate": [final_answer("SELECT count(*) FROM sem.claims", "three")],
        "__selector__": [LLMResponse(text="The grain is wrong.\nCLUSTER c0", usage={"input_tokens": 20, "output_tokens": 4})],
    })
    answer = MultiCandidateAgent(llm, selection="majority_then_pairwise").answer("How many distinct claims?", tools)
    assert answer.rows == [(2,)]
    assert answer.usage["selection"]["selector_called"] is True
    assert answer.usage["input_tokens"] == 20
    assert answer.usage["output_tokens"] == 4


def test_constant_sql_is_not_an_executed_answer():
    tools = _MemTools()
    llm = ScriptedLLM({
        "direct": [final_answer("SELECT 2", "guess")],
        "grain_and_date": [final_answer("SELECT count(DISTINCT claim_key) FROM sem.claims", "two")],
        "filter_then_aggregate": [final_answer("SELECT count(DISTINCT claim_key) FROM sem.claims", "two")],
    })
    answer = MultiCandidateAgent(llm, selection="majority").answer("How many distinct claims?", tools)
    assert answer.rows == [(2,)]
    assert answer.usage["selection"]["n_errors"] == 1


def test_unconfigured_llm_does_not_open_a_client():
    with pytest.raises(RuntimeError, match="paid API"):
        MultiCandidateAgent().answer("How many claims?", _MemTools())


def test_arm_env_selects_n(monkeypatch):
    monkeypatch.setenv("CC_MC_ARM", "mc_n5_majority_then_pairwise")
    agent = MultiCandidateAgent(ScriptedLLM({}))
    assert agent.strategies == (
        "direct", "grain_and_date", "filter_then_aggregate", "unit_of_analysis", "literal_projection",
    )
    assert agent.config["selection"] == "majority_then_pairwise"
