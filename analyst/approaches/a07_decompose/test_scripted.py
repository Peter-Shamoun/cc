"""Scripted-backend tests for A07. No model API, no generator, no lake.

Run from the repo root:

    pytest -q analyst/approaches/a07_decompose/test_scripted.py
"""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest

from analyst.approaches.a07_decompose.adapter import (
    CONFIG_NAME,
    briefing,
    install_prompts,
    prepare_run,
    technique_config,
)
from analyst.approaches.a07_decompose.checks import report_from_aggregate, values_are_monotonic
from analyst.approaches.a07_decompose.prompts import STEP_ROLES, plan_messages, repair_messages
from analyst.approaches.a07_decompose.protocol import (
    GuardedDuckDbExecutor,
    Plan,
    ProtocolError,
    Step,
    plan_from_json,
    run,
)
from analyst.approaches.a07_decompose.scripted import ScriptedBackend
from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
from bench.agent_session import create_session

TOP_CARC_QUESTION = (
    "Which 2 denial reason codes appear on the most denied claims? "
    "Only count CO denial reasons, count each denied claim once per code, and use current adjudications."
)
COUNT_QUESTION = "How many distinct claims have we submitted?"


def lab_connection():
    """A tiny sem.* stand-in. External access is off and the configuration is locked, as in ccsim.query."""
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA sem")
    connection.execute(
        """
        CREATE TABLE sem.remit_claims (
            remit_claim_id VARCHAR, claim_key VARCHAR, payer_name VARCHAR,
            charge_amount DOUBLE, paid_amount DOUBLE,
            is_denied BOOLEAN, is_current_adjudication BOOLEAN
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE sem.adjustments (
            adjustment_id VARCHAR, remit_claim_id VARCHAR, carc_code VARCHAR,
            group_code VARCHAR, is_denial_carc BOOLEAN
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE sem.claims (
            claim_key VARCHAR, charge_amount DOUBLE, is_latest_submission BOOLEAN
        )
        """
    )
    connection.execute(
        """
        INSERT INTO sem.remit_claims VALUES
            ('r1', 'A', 'North', 100, 0, true, true),
            ('r1b', 'A', 'North', 100, 0, true, false),
            ('r2', 'B', 'North', 40, 0, true, true),
            ('r3', 'C', 'North', 10, 10, false, true),
            ('r4', 'D', 'South', 60, 0, true, true)
        """
    )
    connection.execute(
        """
        INSERT INTO sem.adjustments VALUES
            ('a1', 'r1', '16', 'CO', true),
            ('a2', 'r1', '50', 'CO', true),
            ('a3', 'r1b', '97', 'CO', true),
            ('a4', 'r2', '16', 'CO', true),
            ('a5', 'r2', '16', 'CO', true),
            ('a6', 'r3', '45', 'CO', false),
            ('a7', 'r4', '50', 'CO', true),
            ('a8', 'r4', '16', 'CO', true)
        """
    )
    connection.execute(
        """
        INSERT INTO sem.claims VALUES
            ('A', 10, true),
            ('A', 10, false),
            ('B', 20, true)
        """
    )
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    return connection


def executor(connection=None, *, escalate: bool = False) -> GuardedDuckDbExecutor:
    return GuardedDuckDbExecutor(connection or lab_connection(), escalate=escalate)


def population_step(**overrides) -> Step:
    fields = dict(
        role="population",
        cte_name="population",
        purpose="current denied adjudications, one row per remit",
        sql=(
            "SELECT remit_claim_id, claim_key, charge_amount FROM sem.remit_claims "
            "WHERE is_current_adjudication AND is_denied"
        ),
        grain_keys=("remit_claim_id",),
        preserve_sums=("charge_amount",),
        expect_nonempty=True,
    )
    fields.update(overrides)
    return Step(**fields)


def repaired_join() -> Step:
    return Step(
        role="join",
        cte_name="joined",
        purpose="one row per denied claim and CO denial CARC",
        sql=(
            "SELECT DISTINCT p.remit_claim_id, p.claim_key, a.carc_code "
            "FROM population p JOIN sem.adjustments a ON a.remit_claim_id = p.remit_claim_id "
            "WHERE a.group_code = 'CO' AND a.is_denial_carc"
        ),
        grain_keys=("remit_claim_id", "carc_code"),
    )


def fanout_join() -> Step:
    return Step(
        role="join",
        cte_name="joined",
        purpose="attach CARCs without collapsing line duplicates",
        sql=(
            "SELECT p.remit_claim_id, p.claim_key, p.charge_amount, a.carc_code "
            "FROM population p JOIN sem.adjustments a ON a.remit_claim_id = p.remit_claim_id "
            "WHERE a.group_code = 'CO' AND a.is_denial_carc"
        ),
        grain_keys=("remit_claim_id", "carc_code"),
        preserve_sums=("charge_amount",),
    )


def aggregation_step() -> Step:
    return Step(
        role="aggregation",
        cte_name="aggregation",
        purpose="denied claims per CARC",
        sql=(
            "SELECT carc_code, count(DISTINCT remit_claim_id) AS denied_claims "
            "FROM joined GROUP BY carc_code"
        ),
        grain_keys=("carc_code",),
    )


def ranking_step() -> Step:
    return Step(
        role="ranking",
        cte_name="ranking",
        purpose="top 2 CARCs",
        sql=(
            "SELECT carc_code, denied_claims FROM aggregation "
            "ORDER BY denied_claims DESC, carc_code LIMIT 2"
        ),
        order_by="denied_claims",
        order_direction="desc",
        max_rows=2,
    )


def top_carc_plan(join_step: Step) -> Plan:
    return Plan(
        steps=[population_step(), join_step, aggregation_step(), ranking_step()],
        final_select="SELECT carc_code, denied_claims FROM ranking ORDER BY denied_claims DESC, carc_code",
    )


def test_checks_flag_duplicate_grain_sum_mismatch_and_fanout():
    failures = report_from_aggregate(
        role="join", cte_name="joined", columns=["row_count", "distinct_grain", "null_grain_rows", "sum_charge_amount"],
        rows=[(6, 5, 1, 400.0)], grain_keys=("remit_claim_id", "carc_code"), preserve_sums=("charge_amount",),
        expect_nonempty=False, max_rows=None, max_fanout=1.5, parent_row_count=3,
        parent_sums={"charge_amount": 200.0},
    )
    codes = {item.code for item in failures.failures}
    assert codes == {"duplicate_grain", "null_grain", "sum_mismatch", "fanout"}
    assert not failures.ok
    assert failures.row_count == 6


def test_checks_accept_a_preserved_sum_and_a_tied_ranking():
    report = report_from_aggregate(
        role="population", cte_name="population",
        columns=["row_count", "distinct_grain", "null_grain_rows", "sum_charge_amount"],
        rows=[(3, 3, 0, 200.0)], grain_keys=("remit_claim_id",), preserve_sums=("charge_amount",),
        expect_nonempty=True, max_rows=None, max_fanout=None, parent_row_count=None, parent_sums={},
    )
    assert report.ok
    assert report.sums["charge_amount"] == 200.0
    assert values_are_monotonic([3, 2, 2, 1], "desc")
    assert values_are_monotonic([1, 1, 4], "asc")
    assert not values_are_monotonic([3, 4], "desc")
    assert not values_are_monotonic([1, None], "asc")


def test_population_only_count_round_trips_the_planner_json():
    payload = {
        "refuse": False,
        "steps": [{
            "role": "population",
            "cte_name": "population",
            "purpose": "one row per claim_key",
            "sql": "SELECT DISTINCT claim_key FROM sem.claims",
            "grain_keys": ["claim_key"],
            "expect_nonempty": True,
        }],
        "final_select": "SELECT count(*) AS claims FROM population",
    }
    messages = plan_messages(COUNT_QUESTION, "sem.claims(claim_key VARCHAR)", escalate=False)
    assert COUNT_QUESTION in messages[-1]["content"]
    assert all(role in messages[0]["content"] for role in STEP_ROLES)
    backend = ScriptedBackend({COUNT_QUESTION: plan_from_json(payload)})
    query_executor = GuardedDuckDbExecutor(lab_connection())
    result = run(COUNT_QUESTION, backend, query_executor)
    assert result.checks_passed
    assert result.steps[0].report.row_count == 2
    # Score the way the bench does: guard, then execute on the locked connection.
    assert query_executor.execute(result.sql)[1] == [(2,)]


def test_fanout_join_is_repaired_before_the_ranking_is_composed():
    plan = top_carc_plan(fanout_join())
    backend = ScriptedBackend({TOP_CARC_QUESTION: plan}, repairs={(TOP_CARC_QUESTION, "join"): repaired_join()})
    query_executor = GuardedDuckDbExecutor(lab_connection())
    result = run(TOP_CARC_QUESTION, backend, query_executor, schema_text="sem.remit_claims")
    assert result.checks_passed
    assert len(backend.repair_requests) == 1
    repair = backend.repair_requests[0]
    assert repair.step.role == "join"
    assert {item.code for item in repair.report.failures} >= {"duplicate_grain", "sum_mismatch"}
    assert result.steps[1].repairs == 1
    assert result.steps[1].report.row_count == 5
    assert result.steps[0].report.sums["charge_amount"] == 200.0
    rows = query_executor.execute(result.sql)[1]
    assert rows == [("16", 3), ("50", 2)]
    assert "97" not in {row[0] for row in rows}
    assert repair_messages(TOP_CARC_QUESTION, "join", repair.step.sql, repair.report.failure_text(), "")[1]["content"].count("join")


def test_wrong_population_can_pass_every_check():
    """Uniqueness does not know which filter the question asked for. That limit is deliberate."""
    wrong = population_step(
        purpose="superseded adjudications only",
        sql=(
            "SELECT remit_claim_id, claim_key, charge_amount FROM sem.remit_claims "
            "WHERE NOT is_current_adjudication AND is_denied"
        ),
    )
    plan = Plan(
        steps=[wrong, repaired_join(), aggregation_step(), ranking_step()],
        final_select="SELECT carc_code, denied_claims FROM ranking ORDER BY denied_claims DESC, carc_code",
    )
    query_executor = GuardedDuckDbExecutor(lab_connection())
    result = run(TOP_CARC_QUESTION, ScriptedBackend({TOP_CARC_QUESTION: plan}), query_executor)
    assert result.checks_passed
    assert query_executor.execute(result.sql)[1] == [("97", 1)]


def test_empty_population_is_a_failed_check_and_the_query_is_still_returned():
    plan = Plan(steps=[population_step(
        sql="SELECT remit_claim_id, charge_amount FROM sem.remit_claims WHERE payer_name = 'nobody'",
    )])
    result = run(TOP_CARC_QUESTION, ScriptedBackend({TOP_CARC_QUESTION: plan}), executor(), max_repairs=0)
    assert not result.checks_passed
    assert result.sql
    assert result.steps[0].repairs == 0
    assert result.steps[0].report.failures[0].code == "empty"


def test_refusal_does_not_query():
    plan = Plan(steps=[], refuse=True, refusal_reason="Patient names are not in these views.")
    query_executor = executor()
    result = run("List patient names.", ScriptedBackend({"List patient names.": plan}), query_executor)
    assert result.refused and result.sql is None
    assert query_executor.statements == []


def test_illegal_plan_and_illegal_repair_fail_fast():
    with pytest.raises(ProtocolError, match="first step"):
        run("q", ScriptedBackend(default_plan=Plan(steps=[repaired_join()])), executor())
    with pytest.raises(ProtocolError, match="plain identifier"):
        run("q", ScriptedBackend(default_plan=Plan(steps=[population_step(grain_keys=("claim_key; drop",))])), executor())
    backend = ScriptedBackend(
        {COUNT_QUESTION: Plan(steps=[population_step(sql="SELECT DISTINCT claim_key FROM sem.claims", grain_keys=("claim_key",), preserve_sums=())])},
        repairs={"population": Step(role="join", cte_name="other", sql="SELECT 1")},
    )
    # Force a failed check so the illegal repair is invoked: empty grain would not get that far.
    backend.plans[COUNT_QUESTION].steps[0] = population_step(
        sql="SELECT remit_claim_id, charge_amount FROM sem.remit_claims WHERE payer_name = 'nobody'",
    )
    with pytest.raises(ProtocolError, match="keep role"):
        run(COUNT_QUESTION, backend, executor())


def test_guard_rejects_a_step_that_leaves_sem_and_a_final_select_that_reads_a_file():
    result = run("q", ScriptedBackend(default_plan=Plan(steps=[Step(
        role="population", cte_name="population",
        sql="SELECT * FROM read_parquet('s3://bucket/x.parquet')", grain_keys=("claim_key",),
    )])), executor())
    assert not result.checks_passed
    assert result.steps[0].report.failures[0].code == "execution_error"
    assert "not allowed" in result.steps[0].report.failures[0].message

    leaked = Plan(
        steps=[Step(
            role="population", cte_name="population", sql="SELECT DISTINCT claim_key FROM sem.claims", grain_keys=("claim_key",),
        )],
        final_select="SELECT claim_key FROM population UNION ALL SELECT * FROM read_parquet('s3://bucket/x.parquet')",
    )
    guarded = run(COUNT_QUESTION, ScriptedBackend({COUNT_QUESTION: leaked}), executor())
    assert guarded.steps[0].report.ok
    assert not guarded.guarded
    assert "not allowed" in (guarded.guard_error or "")


def test_lossless_is_rejected_until_the_executor_is_escalated():
    step = Step(
        role="population", cte_name="population", sql="SELECT * FROM lossless.edi_source_file", grain_keys=("claim_key",),
    )
    denied = run("q", ScriptedBackend(default_plan=Plan(steps=[step])), executor())
    assert denied.steps[0].report.failures[0].code == "execution_error"
    allowed = run("q", ScriptedBackend(default_plan=Plan(steps=[step])), executor(escalate=True))
    # The guard now accepts the schema. DuckDB then reports the missing table. Still not a silent success.
    assert not allowed.checks_passed
    assert allowed.steps[0].report.failures[0].code == "execution_error"
    assert "not allowed" not in allowed.steps[0].report.failures[0].message


def test_technique_config_is_a_legal_eval_subagent_config_and_the_prompt_is_the_procedure(tmp_path):
    config = technique_config()
    assert set(config) <= set(DEFAULT_TECHNIQUE_CONFIG)
    assert config["glossary"] == "basic"
    assert config["context_style"] == "goal"
    assert config["max_tool_calls"] >= 16
    case = {"id": "q_a07", "question": COUNT_QUESTION, "escalate": False}
    session = create_session(tmp_path, case, config, "haiku", "a07-adapter-scripted")
    text = briefing(session)
    for role in STEP_ROLES:
        assert role in text
    assert "distinct" in text
    assert "sum(" in text
    assert "briefing" in text
    assert "sem.* only" in text
    assert COUNT_QUESTION in text
    assert "python3" in text and "agent_cli.py" in text

    manifest_path = tmp_path / "manifest.json"
    other_session = tmp_path / "other"
    other_session.mkdir()
    (other_session / "session.json").write_text(json.dumps({
        "question": "untouched", "technique_config": {}, "escalate": False,
    }))
    manifest_path.write_text(json.dumps({"entries": [
        {"config": CONFIG_NAME, "session": str(session), "prompt": "ORIGINAL"},
        {"config": "base", "session": str(other_session), "prompt": "KEEP"},
    ]}))
    install_prompts(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    assert "population" in manifest["entries"][0]["prompt"]
    assert manifest["entries"][0]["approach"] == CONFIG_NAME
    assert manifest["entries"][1]["prompt"] == "KEEP"
    assert "approach" not in manifest


def test_prepare_run_asks_eval_subagents_for_this_config_and_rewrites_the_prompt(tmp_path, monkeypatch):
    session = tmp_path / "session"
    session.mkdir()
    (session / "session.json").write_text(json.dumps({
        "question": TOP_CARC_QUESTION,
        "technique_config": technique_config(),
        "escalate": False,
    }))
    manifest_path = tmp_path / "manifest.json"

    def fake_prepare(run_label, configs, models, repeats, only=None, snapshot_path=None):
        assert run_label == "a07-screen"
        assert list(configs) == [CONFIG_NAME]
        assert set(configs[CONFIG_NAME]) <= set(DEFAULT_TECHNIQUE_CONFIG)
        assert models == ["haiku"] and repeats == 2 and only == "hard"
        assert snapshot_path == Path("/tmp/snap")
        manifest_path.write_text(json.dumps({"entries": [
            {"config": CONFIG_NAME, "session": str(session), "prompt": "STANDARD BRIEFING"},
        ]}))
        return manifest_path

    monkeypatch.setattr("bench.eval_subagents.prepare", fake_prepare)
    written = prepare_run("a07-screen", ["haiku"], repeats=2, only="hard", snapshot_path=Path("/tmp/snap"))
    manifest = json.loads(written.read_text())
    assert manifest["approach"] == CONFIG_NAME
    assert "STANDARD BRIEFING" not in manifest["entries"][0]["prompt"]
    assert "WITH population AS" in manifest["entries"][0]["prompt"]
    assert TOP_CARC_QUESTION in manifest["entries"][0]["prompt"]
