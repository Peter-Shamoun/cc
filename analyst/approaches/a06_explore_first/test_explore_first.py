"""Scripted-backend tests for A06. No model API is called."""
from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import duckdb
import pytest
import sqlglot
from sqlglot import exp

from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
from bench.guard import guard

from analyst.approaches.a06_explore_first import (
    APPROACH_ID,
    GuardedExecutor,
    ScriptedBackend,
    apply_protocol,
    augment_prompt,
    executor_for_query_session,
    prepare_eval_run,
    run,
    technique_config,
)
from analyst.approaches.a06_explore_first.catalog import VIEW_BY_NAME
from analyst.approaches.a06_explore_first.probes import maximum_probe_statements
from analyst.approaches.a06_explore_first.prompts import PROTOCOL_HEADER

ROOT = Path(__file__).resolve().parents[3]
SECRET_KEY = "PCN-SECRET"
SECRET_ID = "CID-SECRET"
PAYER_NORTH = "NORTH-PAYER"


def _connection() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA sem")
    connection.execute("""
        CREATE TABLE sem.claims (
            claim_id VARCHAR, claim_key VARCHAR, is_latest_submission BOOLEAN,
            payer_responsibility VARCHAR, charge_amount DOUBLE, submission_date DATE,
            payer_name VARCHAR, payer_key VARCHAR)
    """)
    connection.execute("""
        INSERT INTO sem.claims VALUES
        ('CID-SECRET-1', 'PCN-SECRET-A', true,  'P', 100, DATE '2024-01-15', 'NORTH-PAYER', 'pk1'),
        ('CID-SECRET-2', 'PCN-SECRET-A', false, 'S', 50,  DATE '2024-02-01', 'NORTH-PAYER', 'pk1'),
        ('CID-SECRET-3', 'PCN-SECRET-B', true,  'P', NULL, DATE '2024-03-01', 'SOUTH-PAYER', 'pk2'),
        ('CID-SECRET-4', 'PCN-SECRET-C', true,  'P', 25,  DATE '2024-03-20', 'SOUTH-PAYER', 'pk2')
    """)
    connection.execute("""
        CREATE TABLE sem.claim_lines (
            claim_line_id VARCHAR, claim_id VARCHAR, claim_key VARCHAR, procedure_code VARCHAR,
            line_charge_amount DOUBLE)
    """)
    connection.execute("""
        INSERT INTO sem.claim_lines VALUES
        ('L1', 'CID-SECRET-1', 'PCN-SECRET-A', '80053', 40),
        ('L2', 'CID-SECRET-1', 'PCN-SECRET-A', '85025', 60),
        ('L3', 'CID-SECRET-2', 'PCN-SECRET-A', '80053', 50),
        ('L4', 'CID-SECRET-3', 'PCN-SECRET-B', '80053', 10),
        ('L5', 'CID-SECRET-3', 'PCN-SECRET-B', '87086', 5)
    """)
    return connection


def _plan() -> dict:
    return {
        "views": ["sem.claims", "sem.claim_lines", "sem.not_a_view"],
        "columns": {
            "sem.claims": [
                "claim_key", "is_latest_submission", "payer_responsibility", "charge_amount",
                "submission_date", "nope", "claim_key; drop table sem.claims",
            ],
            "sem.claim_lines": ["claim_id", "procedure_code"],
        },
        "literals": [
            {"view": "sem.claim_lines", "column": "procedure_code", "text": "80053"},
            {"view": "sem.claims", "column": "payer_name", "text": "O'Brien"},
        ],
        "refuse": None,
    }


def _run(connection, plan, sql="SELECT count(DISTINCT claim_key) AS claims FROM sem.claims WHERE is_latest_submission"):
    executor = GuardedExecutor(connection)
    backend = ScriptedBackend(plan, sql, "three claims")
    result = run("How many distinct claims have we submitted, counting each claim once?", executor, backend)
    return result, executor, backend


def test_catalog_column_names_match_the_view_sql():
    text = (ROOT / "claude/schema/02_semantic_layer_views.sql").read_text()
    found = {}
    for part in text.split("CREATE OR REPLACE VIEW ")[1:]:
        view_name = part.split()[0]
        if not view_name.startswith("sem."):
            continue
        trees = [tree for tree in sqlglot.parse("CREATE VIEW " + part, read="duckdb") if tree is not None]
        create = next(tree for tree in trees if isinstance(tree, exp.Create))
        found[view_name] = [expression.alias_or_name for expression in create.args["expression"].expressions]
    assert set(found) == set(VIEW_BY_NAME)
    for view_name, column_names in found.items():
        assert [column.name for column in VIEW_BY_NAME[view_name].columns] == column_names


def test_scripted_exploration_notes_then_final_sql():
    result, executor, backend = _run(_connection(), _plan())
    notes = result.notes
    assert result.final_error is None
    assert result.rows == [(3,)]
    assert result.final_sql.startswith("SELECT count(DISTINCT claim_key)")
    assert "sem.not_a_view" not in result.plan["views"]
    assert "nope" not in result.plan["columns"]["sem.claims"]
    assert any("drop table" in item for item in result.plan["dropped"])

    assert "sem.claims: 4 rows" in notes
    assert "payer_responsibility: null 0/4 (0%); 2 distinct; P x3, S x1" in notes
    assert "is_latest_submission: null 0/4 (0%); 2 distinct; true x3, false x1" in notes
    assert "charge_amount: null 1/4 (25%); 3 distinct; min 25; max 100" in notes
    assert "submission_date: null 0/4 (0%); 4 distinct; range 2024-01-15 .. 2024-03-20" in notes
    assert "claim_key: null 0/4 (0%); 3 distinct; values not listed" in notes
    assert "procedure_code: null 0/5 (0%); 3 distinct; 80053 x3, 85025 x1, 87086 x1" in notes
    assert "joined_rows=5" in notes and "joined_rows=8" in notes
    assert notes.count("multiplies left rows") == 2
    assert 'literal sem.claim_lines.procedure_code containing "80053": 80053 x3' in notes
    assert "no stored value" in notes and "O'Brien" in notes
    for secret in (SECRET_KEY, SECRET_ID, PAYER_NORTH, "SOUTH-PAYER"):
        assert secret not in notes

    plan_prompt = backend.prompts[0][1]
    write_prompt = backend.prompts[1][1]
    assert backend.prompts[0][0] == "plan" and backend.prompts[1][0] == "write_sql"
    assert "one canonical payer" in plan_prompt and "one canonical payer" in write_prompt
    assert SECRET_KEY not in plan_prompt and SECRET_KEY not in write_prompt
    assert "joined_rows=8" not in plan_prompt and "joined_rows=8" in write_prompt
    assert "How many distinct claims" in plan_prompt and "How many distinct claims" in write_prompt

    assert len(result.probe_sql) == 7
    assert len(result.probe_sql) <= maximum_probe_statements()
    assert len(executor.attempted_sql) == 8
    assert executor.attempted_sql[-1] == result.final_sql
    for sql in result.probe_sql:
        guard(sql)
        assert re.search(r"select\s+\*", sql, re.IGNORECASE) is None
    claim_summary = next(sql for sql in result.probe_sql if '"claims"' in sql and "claim_lines" not in sql and "n_rows" in sql)
    line_summary = next(sql for sql in result.probe_sql if "claim_lines" in sql and "n_rows" in sql)
    assert "histogram" in claim_summary.lower()
    assert "histogram" not in line_summary.lower()
    assert any("FULL OUTER JOIN" in sql for sql in result.probe_sql)
    assert any("O''Brien" in sql for sql in result.probe_sql)


def test_refusal_does_not_query():
    connection = _connection()
    executor = GuardedExecutor(connection)
    backend = ScriptedBackend(
        {"refuse": "Patient names are not in the semantic layer.", "views": ["sem.claims"]},
        "SELECT count(*) FROM sem.claims")
    result = run("List patient names.", executor, backend)
    assert result.refused and "Patient names" in result.refusal_reason
    assert result.final_sql is None and executor.attempted_sql == []
    assert len(backend.prompts) == 1


def test_failed_probe_is_noted_and_final_sql_still_runs():
    result, executor, _backend = _run(
        _connection(),
        {"views": ["sem.payers"], "columns": {"sem.payers": ["payer_class"]}, "literals": [], "refuse": None},
        "SELECT count(*) AS n FROM sem.claims")
    assert "sem.payers: probe failed:" in result.notes
    assert result.final_error is None and result.rows == [(4,)]
    assert len(result.probe_sql) == 1 and len(executor.attempted_sql) == 2


def test_final_sql_that_breaks_the_guard_does_not_run():
    connection = _connection()
    result, _executor, _backend = _run(
        connection, _plan(), "SELECT count(*) FROM sem.claims; DROP TABLE sem.claims")
    assert result.rows is None and "exactly one statement" in result.final_error
    assert connection.execute("SELECT count(*) FROM sem.claims").fetchone()[0] == 4


class _RecordingConnection:
    def __init__(self, connection):
        self.connection = connection
        self.executed_sql = []

    def execute(self, sql, *args, **kwargs):
        self.executed_sql.append(sql)
        return self.connection.execute(sql, *args, **kwargs)


def test_guard_rejects_a_file_read_before_duckdb():
    connection = _connection()
    recorder = _RecordingConnection(connection)
    executor = GuardedExecutor(recorder)
    backend = ScriptedBackend(_plan(), "SELECT * FROM read_parquet('s3://secret/phi.parquet')", "no")
    result = run("How many distinct claims have we submitted?", executor, backend)
    assert result.rows is None and "read_parquet" in result.final_error
    assert recorder.executed_sql
    assert all("read_parquet" not in sql.lower() for sql in recorder.executed_sql)


def test_executor_uses_the_query_session_connection():
    connection = duckdb.connect()
    session = SimpleNamespace(con=connection, customer="acme_labs")
    executor = executor_for_query_session(session, escalate=False)
    assert executor.connection is connection
    with pytest.raises(TypeError):
        executor_for_query_session(SimpleNamespace(customer="acme_labs"))


def test_eval_subagents_config_is_the_base_arm_plus_the_protocol():
    assert technique_config() == DEFAULT_TECHNIQUE_CONFIG
    assert technique_config()["data_profile"] is False
    assert technique_config()["enable_sql"] is True
    assert technique_config()["glossary"] == "basic"
    assert technique_config()["context_style"] == "goal"
    briefing = "Commands:\nrun sql\n\nUser question: How many distinct claims?\n"
    rewritten = augment_prompt(briefing)
    assert rewritten.index(PROTOCOL_HEADER) < rewritten.index("User question:")
    assert rewritten.endswith("How many distinct claims?\n")
    assert "null rates" in rewritten and "join cardinalit" in rewritten
    assert "Invariants and common traps" not in rewritten
    assert augment_prompt(rewritten) == rewritten
    manifest = apply_protocol({"entries": [{"prompt": briefing, "config": APPROACH_ID}]})
    assert manifest["approach"] == APPROACH_ID
    assert manifest["entries"][0]["approach"] == APPROACH_ID
    assert PROTOCOL_HEADER in manifest["entries"][0]["prompt"]


def test_prepare_eval_run_rewrites_manifest_prompts(monkeypatch, tmp_path):
    seen = {}

    def fake_prepare(run_label, configs, models, repeats, only=None, snapshot_path=None):
        seen["configs"] = configs
        seen["models"] = models
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps({"entries": [{"prompt": "User question: Q?\n", "config": APPROACH_ID}]}))
        return path

    import bench.eval_subagents as eval_subagents
    monkeypatch.setattr(eval_subagents, "prepare", fake_prepare)
    manifest_path = prepare_eval_run("a06-pilot", ["haiku"], 1)
    manifest = json.loads(manifest_path.read_text())
    assert seen["configs"] == {APPROACH_ID: DEFAULT_TECHNIQUE_CONFIG}
    assert seen["models"] == ["haiku"]
    assert PROTOCOL_HEADER in manifest["entries"][0]["prompt"]
    assert manifest["entries"][0]["prompt"].rstrip().endswith("Q?")


def test_wide_code_is_counted_and_not_listed():
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA sem")
    connection.execute("CREATE TABLE sem.claim_lines (procedure_code VARCHAR)")
    connection.execute("INSERT INTO sem.claim_lines VALUES " + ", ".join(f"('{80000 + i}')" for i in range(13)))
    result, _executor, _backend = _run(
        connection,
        {"views": ["sem.claim_lines"], "columns": {"sem.claim_lines": ["procedure_code"]}, "literals": [], "refuse": None},
        "SELECT count(*) FROM sem.claim_lines")
    assert "13 distinct; values not listed" in result.notes
    assert "80000" not in result.notes
    assert len(result.probe_sql) == 1
    assert "histogram" not in result.probe_sql[0].lower()


def test_package_does_not_call_a_paid_api():
    package = Path(__file__).resolve().parent
    source = "\n".join(path.read_text() for path in package.glob("*.py") if not path.name.startswith("test_"))
    lowered = source.lower()
    for banned in ("anthropic", "openai", "messages.create", "api_key"):
        assert banned not in lowered


def test_empty_question_fails_fast():
    with pytest.raises(ValueError):
        run("  ", GuardedExecutor(duckdb.connect()), ScriptedBackend({}, "SELECT 1"))


def test_fenced_plan_json_is_accepted():
    from analyst.approaches.a06_explore_first.protocol import parse_plan
    plan = parse_plan('```json\n{"refuse": "patient names are not available"}\n```')
    assert plan.refusal_reason == "patient names are not available"
    assert plan.views == []
