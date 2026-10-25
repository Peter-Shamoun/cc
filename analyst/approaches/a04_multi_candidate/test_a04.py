"""Scripted-backend tests for A04. No model API is constructed or called."""
from __future__ import annotations

import json
from decimal import Decimal

import duckdb
import pytest

from analyst.approaches.a04_multi_candidate.adapter import (
    complete_manifest,
    complete_session,
    configs,
    technique_config,
)
from analyst.approaches.a04_multi_candidate.backend import ScriptedBackend
from analyst.approaches.a04_multi_candidate.executor import executor_for_query_session
from analyst.approaches.a04_multi_candidate.prompts import STRATEGIES, generation_purpose
from analyst.approaches.a04_multi_candidate.protocol import (
    AGREEMENT_MODEL_CALLS,
    SELECTOR_MODEL_CALLS,
    parse_choice,
    parse_generation,
    run_protocol,
)
from analyst.approaches.a04_multi_candidate.selector import (
    Candidate,
    fingerprint,
    is_hard_warning,
    sanity_warnings,
    select_candidates,
)
from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
from bench.agent_session import SESSIONS_ROOT, create_session, score_session
from bench.agents import Tools
from bench.dataset import open_snapshot

GOLD = ("SELECT count(DISTINCT claim_key) AS n FROM sem.claims "
        "WHERE is_latest_submission AND payer_responsibility = 'P'")
WRONG_ALL = "SELECT count(*) AS n FROM sem.claims"
WRONG_LATEST = "SELECT count(DISTINCT claim_key) AS n FROM sem.claims WHERE is_latest_submission"
EMPTY = "SELECT claim_key FROM sem.claims WHERE false"
RATE_BAD = "SELECT 250.0 AS denial_rate FROM sem.claims"
RATE_OK = "SELECT 25.0 AS denial_rate FROM sem.claims"
SCHEMA = "SCHEMA_SENTINEL\nsem.claims(claim_key VARCHAR, is_latest_submission BOOLEAN, payer_responsibility VARCHAR)"
QUESTION = "QUESTION_TOKEN how many distinct claims have a latest primary-payer submission?"


def _db():
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA sem")
    connection.execute("""
        CREATE VIEW sem.claims AS SELECT * FROM (VALUES
            ('c1', true, 'P'),
            ('c1', false, 'P'),
            ('c2', true, 'S'),
            ('c3', true, 'P')
        ) t(claim_key, is_latest_submission, payer_responsibility)
    """)
    return connection


class _Session:
    def __init__(self, connection):
        self.con = connection


def _sql(strategy: str, sql: str, rows, warnings=(), columns=("n",)) -> Candidate:
    return Candidate(strategy=strategy, sql=sql, columns=tuple(columns), rows=tuple(rows), warnings=tuple(warnings))


def _refuse(strategy: str, reason: str = "PHI") -> Candidate:
    return Candidate(strategy=strategy, refused=True, refusal_reason=reason)


def _no_selector(left, right):
    raise AssertionError(f"selector should not run ({left.strategy} vs {right.strategy})")


def _responses(**sql_by_strategy):
    return {generation_purpose(strategy): json.dumps({"sql": sql, "notes": strategy})
            for strategy, sql in sql_by_strategy.items()}


def _run(backend, connection=None):
    connection = _db() if connection is None else connection
    return run_protocol(QUESTION, SCHEMA, backend, executor_for_query_session(_Session(connection)))


def test_config_is_a_legal_eval_subagents_config():
    config = technique_config()
    assert set(config) <= set(DEFAULT_TECHNIQUE_CONFIG)
    assert config["glossary"] == "basic"
    assert config["context_style"] == "goal"
    assert config["effort"] == "low"
    assert config["enable_sql"] is True
    assert config["enable_examples"] is False
    assert config["enable_metrics"] is False
    assert config["result_checks"] is False
    assert configs() == {"a04_multi_candidate": config}


def test_fingerprint_ignores_names_order_and_integral_floats():
    assert fingerprint(("n",), [(2,), (1,)]) == fingerprint(("claims",), [(1,), (2.0,)])
    assert fingerprint(("n",), [(Decimal("2"),)]) == fingerprint(("n",), [(2,)])
    assert fingerprint(("a", "b"), [(1, 2)]) != fingerprint(("a", "b"), [(2, 1)])


def test_sanity_codes_match_the_vote_rules():
    assert any(is_hard_warning(w) for w in sanity_warnings("SELECT 1", ("n",), ()))
    assert not any(is_hard_warning(w) for w in sanity_warnings("SELECT 1", ("n",), ((0,),)))
    assert not any(is_hard_warning(w) for w in sanity_warnings("SELECT 1", ("denial_rate",), ((Decimal("100"),),)))
    assert any(w.startswith("rate_out_of_range:") for w in sanity_warnings(
        "SELECT 1", ("denial_rate",), ((Decimal("100.1"),),)))
    assert any(w.startswith("rate_out_of_range:") for w in sanity_warnings(
        "SELECT 1", ("denial_rate",), ((Decimal("-0.1"),),)))
    assert any(w.startswith("all_null_column:") for w in sanity_warnings(
        "SELECT 1", ("payer_name",), ((None,), (None,))))
    assert not any(is_hard_warning(w) for w in sanity_warnings("SELECT 1", ("payer_name",), ((None,),)))
    soft = sanity_warnings("SELECT count(*) AS n FROM sem.claims", ("n",), ((4,),))
    assert any(w.startswith("missing_latest_submission:") for w in soft)
    assert not any(is_hard_warning(w) for w in soft)
    assert not any(w.startswith("missing_latest_submission:") for w in sanity_warnings(GOLD, ("n",), ((2,),)))


def test_agreement_picks_the_earliest_strategy_without_a_selector():
    decision = select_candidates([
        _sql("direct", "SELECT 2 AS n", ((2,),)),
        _sql("query_plan", "SELECT 2 AS claims", ((2,),)),
        _sql("divide_and_conquer", "SELECT 2.0 AS n", ((2.0,),)),
    ], _no_selector)
    assert decision.kind == "answer" and decision.selector_used is False
    assert decision.strategy == "direct"
    assert decision.reason.startswith("agreement:")


def test_majority_beats_a_single_dissenter():
    decision = select_candidates([
        _sql("direct", GOLD, ((2,),)),
        _sql("query_plan", GOLD, ((2,),)),
        _sql("divide_and_conquer", WRONG_ALL, ((4,),)),
    ], _no_selector)
    assert decision.rows == ((2,),) and decision.reason.startswith("majority:")


def test_sanity_drops_an_empty_majority():
    empty = ("empty_result: the query returned no rows",)
    decision = select_candidates([
        _sql("direct", EMPTY, (), empty, columns=("claim_key",)),
        _sql("query_plan", EMPTY, (), empty, columns=("claim_key",)),
        _sql("divide_and_conquer", GOLD, ((2,),)),
    ], _no_selector)
    assert decision.strategy == "divide_and_conquer"
    assert decision.reason.startswith("sanity:")
    assert decision.selector_used is False


def test_pairwise_on_a_three_way_split_drops_the_third_cluster():
    seen = {}

    def choose(left, right):
        seen["pair"] = (left.strategy, right.strategy)
        return "a", "the plan matches the population"

    direct = _sql("direct", WRONG_ALL, ((4,),), ("missing_latest_submission: sem.claims used without is_latest_submission",))
    decision = select_candidates([
        direct,
        _sql("query_plan", GOLD, ((2,),)),
        _sql("divide_and_conquer", WRONG_LATEST, ((3,),)),
    ], choose)
    assert seen["pair"] == ("query_plan", "divide_and_conquer")
    assert decision.strategy == "query_plan" and decision.selector_used
    assert decision.dropped_strategies == ("direct",)
    assert decision.reason.startswith("pairwise:")


def test_selector_abstain_returns_no_sql():
    decision = select_candidates([
        _sql("direct", WRONG_ALL, ((4,),)),
        _sql("query_plan", GOLD, ((2,),)),
        _sql("divide_and_conquer", WRONG_LATEST, ((3,),)),
    ], lambda left, right: ("abstain", "neither population is right"))
    assert decision.kind == "abstain" and decision.sql is None and decision.selector_used


def test_two_refusals_veto_a_successful_sql():
    decision = select_candidates([
        _refuse("direct"), _refuse("query_plan", "PHI: names"), _sql("divide_and_conquer", GOLD, ((2,),)),
    ], _no_selector)
    assert decision.kind == "refuse" and "PHI: names" in decision.reason


def test_one_refusal_does_not_veto_agreement():
    decision = select_candidates([
        _refuse("direct"), _sql("query_plan", GOLD, ((2,),)), _sql("divide_and_conquer", GOLD, ((2,),)),
    ], _no_selector)
    assert decision.kind == "answer" and decision.rows == ((2,),)


def test_soft_warning_does_not_override_a_majority():
    soft = ("missing_latest_submission: sem.claims used without is_latest_submission",)
    decision = select_candidates([
        _sql("direct", WRONG_ALL, ((4,),), soft),
        _sql("query_plan", WRONG_ALL, ((4,),), soft),
        _sql("divide_and_conquer", GOLD, ((2,),)),
    ], _no_selector)
    assert decision.rows == ((4,),) and decision.selector_used is False


def test_parse_accepts_a_fence_and_a_refusal_and_fails_closed_on_the_selector():
    sql, notes, refusal = parse_generation("```json\n" + json.dumps({"sql": " SELECT 1 ", "notes": "n"}) + "\n```")
    assert (sql, notes, refusal) == ("SELECT 1", "n", None)
    assert parse_generation('{"refuse": "PHI", "sql": "SELECT 1"}')[2] == "PHI"
    assert parse_choice('{"choice": "B", "reason": "rows"}') == ("b", "rows")
    assert parse_choice('{"choice": "first"}')[0] == "abstain"
    with pytest.raises(ValueError):
        parse_choice("I prefer the first query")


def test_executed_agreement_does_not_call_the_selector_and_shares_a_prefix():
    same_result_other_name = ("SELECT count(DISTINCT claim_key) AS claims FROM sem.claims "
                              "WHERE is_latest_submission AND payer_responsibility = 'P'")
    backend = ScriptedBackend({
        generation_purpose("direct"): "```json\n" + json.dumps({"sql": GOLD, "notes": "direct"}) + "\n```",
        generation_purpose("query_plan"): json.dumps({"sql": same_result_other_name, "notes": "grain: claim"}),
        generation_purpose("divide_and_conquer"): json.dumps({"sql": GOLD, "notes": "filter then count"}),
    })
    decision = _run(backend)
    assert decision.kind == "answer" and decision.rows == [(2,)]
    assert decision.selector_used is False and decision.usage["model_calls"] == AGREEMENT_MODEL_CALLS == 3
    assert decision.reason.startswith("agreement:")
    generations = [call for call in backend.calls if call["purpose"].startswith("generate:")]
    assert [call["purpose"] for call in generations] == [generation_purpose(s) for s in STRATEGIES]
    assert len({call["system"] for call in generations}) == 1
    assert "SCHEMA_SENTINEL" in generations[0]["system"]
    assert "QUESTION_TOKEN" not in generations[0]["system"]
    assert len({call["user"] for call in generations}) == 3
    assert all("QUESTION_TOKEN" in call["user"] and "SCHEMA_SENTINEL" not in call["user"] for call in generations)
    assert all(call["purpose"] != "select" for call in backend.calls)


def test_disagreement_asks_the_selector_for_one_pair_and_shows_the_rows():
    # All three mention is_latest_submission, so none is dropped for a soft warning.
    # Strategy order then shows direct and query_plan, and leaves divide_and_conquer out.
    direct_sql = "SELECT count(*) AS n FROM sem.claims WHERE is_latest_submission OR true"
    # Not a substring of GOLD. WRONG_LATEST is, so a substring check would lie.
    divide_sql = "SELECT count(DISTINCT claim_key) AS latest_any_payer FROM sem.claims WHERE is_latest_submission"

    def select(*, system, user, purpose):
        assert purpose == "select"
        assert direct_sql in user and GOLD in user and divide_sql not in user
        assert "(2,)" in user and "(4,)" in user
        return json.dumps({"choice": "b", "reason": "primary latest submissions"})

    backend = ScriptedBackend({
        generation_purpose("direct"): json.dumps({"sql": direct_sql, "notes": "all rows"}),
        generation_purpose("query_plan"): json.dumps({"sql": GOLD, "notes": "primary latest"}),
        generation_purpose("divide_and_conquer"): json.dumps({"sql": divide_sql, "notes": "latest any payer"}),
        "select": select,
    })
    decision = _run(backend)
    assert decision.rows == [(2,)] and decision.selected_strategy == "query_plan"
    assert decision.dropped_strategies == ("divide_and_conquer",)
    assert decision.usage["model_calls"] == SELECTOR_MODEL_CALLS == 4
    assert decision.usage["selector_calls"] == 1


def test_unparseable_selector_abstains():
    backend = ScriptedBackend({
        **_responses(direct=WRONG_ALL, query_plan=GOLD, divide_and_conquer=WRONG_LATEST),
        "select": "I like the first query.",
    })
    decision = _run(backend)
    assert decision.abstained and decision.sql is None and decision.refused is False
    assert decision.usage["selector_calls"] == 1


def test_empty_majority_loses_to_the_only_sane_result():
    backend = ScriptedBackend(_responses(direct=EMPTY, query_plan=EMPTY, divide_and_conquer=GOLD))
    decision = _run(backend)
    assert decision.rows == [(2,)] and decision.selector_used is False
    assert decision.reason.startswith("sanity:")


def test_out_of_range_rate_does_not_win_by_majority():
    backend = ScriptedBackend(_responses(direct=RATE_BAD, query_plan=RATE_BAD, divide_and_conquer=RATE_OK))
    decision = _run(backend)
    assert decision.selected_strategy == "divide_and_conquer" and decision.selector_used is False
    assert any(is_hard_warning(w) for w in decision.candidates[0].warnings)
    assert decision.rows and all(float(value) == 25.0 for row in decision.rows for value in row)


def test_a_guard_failure_and_an_ungrounded_select_are_not_votes():
    backend = ScriptedBackend({
        generation_purpose("direct"): json.dumps({"sql": "SELECT * FROM read_parquet('s3://secret/phi.parquet')"}),
        generation_purpose("query_plan"): json.dumps({"sql": "SELECT 2 AS n"}),
        generation_purpose("divide_and_conquer"): json.dumps({"sql": GOLD}),
    })
    decision = _run(backend)
    assert decision.rows == [(2,)] and decision.selected_strategy == "divide_and_conquer"
    assert "not allowed" in decision.candidates[0].error
    assert decision.candidates[1].error == "query reads no schema-qualified table"


def test_unparseable_generation_is_skipped():
    backend = ScriptedBackend({
        generation_purpose("direct"): "not json",
        generation_purpose("query_plan"): json.dumps({"sql": GOLD}),
        generation_purpose("divide_and_conquer"): json.dumps({"sql": GOLD}),
    })
    decision = _run(backend)
    assert decision.rows == [(2,)]
    assert decision.candidates[0].error.startswith("unparseable generation")


def test_majority_refusal_ignores_a_query_that_ran():
    backend = ScriptedBackend({
        generation_purpose("direct"): json.dumps({"refuse": "PHI"}),
        generation_purpose("query_plan"): json.dumps({"refuse": "PHI: names"}),
        generation_purpose("divide_and_conquer"): json.dumps({"sql": GOLD}),
    })
    decision = _run(backend)
    assert decision.refused and decision.sql is None
    assert decision.usage["model_calls"] == 3


def test_query_session_executor_applies_the_guard():
    session = _Session(_db())
    executed = executor_for_query_session(session).execute(GOLD)
    assert executed.error is None and executed.rows == ((2,),)
    banned = executor_for_query_session(session).execute("SELECT * FROM read_parquet('s3://secret/phi.parquet')")
    assert banned.error and "not allowed" in banned.error
    blocked = executor_for_query_session(session, escalate=False).execute("SELECT claim_key FROM lossless.secret")
    assert blocked.error and "not allowed" in blocked.error
    escalated = executor_for_query_session(session, escalate=True).execute("SELECT claim_key FROM lossless.secret")
    assert escalated.error and "not allowed" not in escalated.error


def test_tools_executor_logs_a_guard_failure_and_still_answers():
    tools = Tools(_db())
    backend = ScriptedBackend({
        generation_purpose("direct"): json.dumps({"sql": "SELECT * FROM read_parquet('s3://secret/phi.parquet')"}),
        generation_purpose("query_plan"): json.dumps({"sql": GOLD}),
        generation_purpose("divide_and_conquer"): json.dumps({"sql": GOLD}),
    })
    decision = run_protocol(QUESTION, SCHEMA, backend, _tools(tools))
    assert decision.rows == [(2,)]
    assert len(tools.calls) == 3
    assert sum(not call["ok"] for call in tools.calls) == 1


def _tools(tools):
    from analyst.approaches.a04_multi_candidate.executor import ToolsExecutor
    return ToolsExecutor(tools)


def _snapshot(tmp_path):
    connection = _db()
    data = tmp_path / "data" / "sem"
    data.mkdir(parents=True)
    target = data / "sem.claims.parquet"
    connection.execute(f"COPY (SELECT * FROM sem.claims) TO '{target}' (FORMAT PARQUET)")
    (tmp_path / "meta.json").write_text(json.dumps({"relations": ["sem.claims"]}))
    return tmp_path


def _case(question, expect_refusal=False):
    return {"id": "a04_primary" if not expect_refusal else "a04_phi", "question": question,
            "gold_sql": GOLD, "category": "claims", "difficulty": "hard", "expect_refusal": expect_refusal}


def test_adapter_completes_a_prepared_manifest_and_the_harness_scores_it(tmp_path):
    snap = _snapshot(tmp_path)
    run = "a04-scripted"
    answer_case = _case(QUESTION)
    refusal_case = _case("List the member names on each claim", expect_refusal=True)
    answer_session = create_session(snap, answer_case, technique_config(), "scripted", run)
    refusal_session = create_session(snap, refusal_case, technique_config(), "scripted", run)

    def respond(*, system, user, purpose):
        if purpose == "select":
            raise AssertionError("scripted agreement should not select")
        if "member names" in user:
            return json.dumps({"refuse": "PHI: member names are not in these views"})
        return json.dumps({"sql": GOLD, "notes": purpose})

    manifest = SESSIONS_ROOT / run / "manifest.json"
    manifest.write_text(json.dumps({"entries": [
        {"session": str(answer_session), "config": "a04_multi_candidate"},
        {"session": str(refusal_session), "config": "a04_multi_candidate"},
        {"session": str(answer_session), "config": "some_other_arm"},
    ]}))
    backend = ScriptedBackend({purpose: respond for purpose in
                               [generation_purpose(s) for s in STRATEGIES] + ["select"]})
    assert complete_manifest(run, backend) == 2

    gold_rows = open_snapshot(snap).execute(GOLD).fetchall()
    scored = score_session(answer_session, answer_case, gold_rows)
    assert scored["correct"] and scored["tool_calls"] == 3 and scored["via"] == "a04"
    assert score_session(refusal_session, refusal_case, None)["correct"]
    record = json.loads((answer_session / "a04_decision.json").read_text())
    assert record["selector_used"] is False and len(record["candidates"]) == 3
    with pytest.raises(FileExistsError):
        complete_session(answer_session, backend)


def test_missing_backend_fails_fast():
    from analyst.approaches.a04_multi_candidate import MultiCandidateAnalyst
    with pytest.raises(RuntimeError):
        MultiCandidateAnalyst().answer("how many claims?", Tools(_db()))
