"""Scripted-LLM tests for the typed compiler. No network, no simulator."""
from __future__ import annotations

import duckdb
import sqlglot
from sqlglot import exp

from analyst.variants.typed_metric_compiler.agent import TypedCompilerAgent
from analyst.variants.typed_metric_compiler.compiler import compile_spec, load_api
from analyst.variants.typed_metric_compiler.config import CompilerConfig
from analyst.variants.typed_metric_compiler.coverage import covers
from analyst.variants.typed_metric_compiler.llm import ScriptedLLM
from analyst.variants.typed_metric_compiler.spec import spec_from_dict

# Columns the compiler reads while choosing a view. Types matter only for the rows we execute.
_COLUMNS = {
    "remit_claims": {
        "claim_key": "VARCHAR", "claim_payer_key": "VARCHAR", "payer_name": "VARCHAR", "payer_key": "VARCHAR",
        "is_current_adjudication": "BOOLEAN", "is_denied": "BOOLEAN", "is_reversal": "BOOLEAN",
        "paid_amount": "DOUBLE", "charge_amount": "DOUBLE", "payment_date": "DATE", "payment_month": "DATE",
        "service_date_from": "DATE", "payer_received_date": "DATE", "check_or_eft_number": "VARCHAR",
        "payer_claim_control_number": "VARCHAR",
    },
    "remit_lines": {
        "claim_key": "VARCHAR", "claim_payer_key": "VARCHAR", "procedure_code": "VARCHAR",
        "is_current_adjudication": "BOOLEAN", "line_is_denied": "BOOLEAN", "claim_is_denied": "BOOLEAN",
        "line_paid_amount": "DOUBLE", "service_date": "DATE", "payment_date": "DATE",
    },
    "claims": {
        "claim_key": "VARCHAR", "is_latest_submission": "BOOLEAN", "has_remit": "BOOLEAN",
        "payer_name": "VARCHAR", "charge_amount": "DOUBLE", "submission_date": "DATE",
        "service_date_from": "DATE", "frequency": "VARCHAR",
    },
    "claim_lines": {"claim_key": "VARCHAR", "is_latest_submission": "BOOLEAN", "procedure_code": "VARCHAR",
                    "line_charge_amount": "DOUBLE"},
    "adjustments": {"claim_key": "VARCHAR", "payer_key": "VARCHAR", "is_current_adjudication": "BOOLEAN",
                    "carc_code": "VARCHAR", "group_code": "VARCHAR", "is_denial_carc": "BOOLEAN",
                    "adjustment_amount": "DOUBLE", "payment_date": "DATE"},
    "remarks": {"claim_key": "VARCHAR", "is_current_adjudication": "BOOLEAN", "remark_code": "VARCHAR"},
    "payments": {"is_balanced": "BOOLEAN", "total_payment_amount": "DOUBLE", "provider_adjustment_amount": "DOUBLE"},
    "provider_adjustments": {"plb_reason_code": "VARCHAR", "provider_adjustment_amount": "DOUBLE"},
    "diagnoses": {"claim_key": "VARCHAR", "is_latest_submission": "BOOLEAN", "diagnosis_code": "VARCHAR"},
    "payers": {"payer_key": "VARCHAR", "payer_name": "VARCHAR"},
}


class Tools:
    def __init__(self, con):
        self._con = con
        self.calls = []

    def sql(self, query, _final=False):
        from bench.guard import guard
        safe = guard(query, 1000, False)
        cur = self._con.execute(safe)
        self.calls.append(query)
        return [d[0] for d in cur.description], cur.fetchall()


def stub() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA sem")
    for name, cols in _COLUMNS.items():
        body = ", ".join(f"{col} {typ}" for col, typ in cols.items())
        con.execute(f"CREATE TABLE sem.{name} ({body})")
    return con


def _outer_names(sql: str) -> list[str]:
    tree = sqlglot.parse_one(sql, read="duckdb")
    return [e.alias_or_name for e in tree.selects]


def test_denial_rate_compiles_without_the_catalog_floor_and_without_extra_numbers():
    con = stub()
    con.execute("INSERT INTO sem.remit_claims VALUES "
                "('a','a|p','P','p', TRUE, TRUE, FALSE, 0, 10, DATE '2026-07-01', DATE '2026-07-01', NULL, NULL, NULL, NULL),"
                "('b','b|p','P','p', TRUE, FALSE, FALSE, 5, 10, DATE '2026-07-02', DATE '2026-07-01', NULL, NULL, NULL, NULL),"
                "('c','c|p','P','p', FALSE, TRUE, TRUE, 0, 10, DATE '2026-06-01', DATE '2026-06-01', NULL, NULL, NULL, NULL)")
    module, api = load_api(con, CompilerConfig())
    spec = spec_from_dict({"intent": "aggregate", "metrics": ["denial_rate"], "date_basis": "none"})
    result = compile_spec(module, api, spec, CompilerConfig())
    assert result.lane == "typed"
    assert _outer_names(result.sql) == ["denial_rate"]
    assert "is_current_adjudication" in result.sql
    assert ">= 20" not in result.sql
    rows = con.execute(result.sql).fetchall()
    assert rows == [(0.5,)]


def test_grouped_rate_keeps_a_small_group_unless_the_question_sets_a_floor():
    con = stub()
    con.execute("INSERT INTO sem.remit_claims (claim_key, claim_payer_key, payer_name, is_current_adjudication, is_denied) "
                "VALUES ('a','a|p','Only', TRUE, TRUE)")
    module, api = load_api(con, CompilerConfig())
    spec = spec_from_dict({"intent": "aggregate", "metrics": ["denial_rate"], "group_by": ["payer_name"],
                           "date_basis": "payment", "order_by": [{"field": "denial_rate", "direction": "desc"}]})
    sql = compile_spec(module, api, spec, CompilerConfig()).sql
    assert _outer_names(sql) == ["payer_name", "denial_rate"]
    assert con.execute(sql).fetchall() == [("Only", 1.0)]
    floored = spec_from_dict({"intent": "aggregate", "metrics": ["denial_rate"], "group_by": ["payer_name"],
                              "date_basis": "none", "min_group_size": 20})
    empty = compile_spec(module, api, floored, CompilerConfig()).sql
    assert ">= 20" in empty
    assert con.execute(empty).fetchall() == []


def test_payer_received_is_an_overlay_and_file_received_is_not_compiled():
    con = stub()
    module, api = load_api(con, CompilerConfig())
    spec = spec_from_dict({"intent": "aggregate", "metrics": ["paid_amount"], "date_basis": "payer_received",
                           "time_start": "2026-07-01", "time_end": "2026-07-31"})
    sql = compile_spec(module, api, spec, CompilerConfig()).sql
    assert "payer_received_date" in sql
    refused = spec_from_dict({"intent": "aggregate", "metrics": ["paid_amount"], "date_basis": "file_received",
                              "time_start": "2026-07-01", "time_end": "2026-07-31"})
    try:
        compile_spec(module, api, refused, CompilerConfig())
    except Exception as e:
        assert "payment_date" in str(e) or "File arrival" in str(e)
    else:
        raise AssertionError("file_received was compiled")


def test_cohort_counts_first_submission_not_the_latest_row():
    con = stub()
    con.execute("INSERT INTO sem.claims VALUES "
                "('A', FALSE, FALSE, 'P', 1, DATE '2026-07-02', DATE '2026-06-01', 'original'),"
                "('A', TRUE, FALSE, 'Q', 1, DATE '2026-08-01', DATE '2026-06-01', 'replacement'),"
                "('B', TRUE, FALSE, 'P', 1, DATE '2026-08-10', DATE '2026-08-01', 'original')")
    module, api = load_api(con, CompilerConfig())
    spec = spec_from_dict({"intent": "aggregate", "metrics": ["cohort_claims"], "date_basis": "cohort_submission",
                           "time_start": "2026-07-01", "time_end": "2026-07-31"})
    sql = compile_spec(module, api, spec, CompilerConfig()).sql
    assert "min(submission_date)" in sql
    assert con.execute(sql).fetchall() == [(1,)]
    narrowed = spec_from_dict({"intent": "aggregate", "metrics": ["submitted_claims"],
                               "filters": [{"field": "has_remit", "op": "=", "value": False}],
                               "date_basis": "cohort_submission", "time_start": "2026-07-01", "time_end": "2026-07-31"})
    narrowed_sql = compile_spec(module, api, narrowed, CompilerConfig()).sql
    assert "claim_key IN" in narrowed_sql
    assert con.execute(narrowed_sql).fetchall() == [(1,)]


def test_claim_key_cannot_be_a_group_and_fallback_cannot_project_it():
    con = stub()
    module, api = load_api(con, CompilerConfig())
    spec = spec_from_dict({"intent": "aggregate", "metrics": ["submitted_claims"], "group_by": ["claim_key"],
                           "date_basis": "none"})
    try:
        compile_spec(module, api, spec, CompilerConfig())
    except Exception as e:
        assert "claim_key" in str(e)
    else:
        raise AssertionError("grouped by claim_key")
    question = "How many claims?"
    agent = TypedCompilerAgent(llm=ScriptedLLM({question: [
        {"tool": "submit_spec", "input": {"intent": "cannot_express", "date_basis": "none", "reason": "need keys"}},
        {"tool": "submit_sql", "input": {"sql": "SELECT claim_key FROM sem.claims"}},
    ]}))
    answer = agent.answer(question, Tools(con))
    assert answer.rows is None
    assert "identifier" in answer.text


def test_phi_and_other_customer_refuse_before_sql():
    agent = TypedCompilerAgent(llm=ScriptedLLM({}))
    phi = agent.answer("List the names and dates of birth of patients whose claims were denied last month.", object())
    assert phi.refused and phi.sql is None
    other = agent.answer("What is Acme Diagnostics' denial rate?", object())
    assert other.refused
    write = agent.answer("Delete the re-delivered duplicate 835 files from the data.", object())
    assert write.refused


def test_unknown_metric_is_repaired_once_then_compiled():
    con = stub()
    con.execute("INSERT INTO sem.payments (is_balanced) VALUES (TRUE), (FALSE)")
    question = "How many unbalanced payments?"
    agent = TypedCompilerAgent(llm=ScriptedLLM({question: [
        {"tool": "submit_spec", "input": {"intent": "aggregate", "metrics": ["not_a_metric"], "date_basis": "none"}},
        {"tool": "submit_spec", "input": {"intent": "aggregate", "metrics": ["unbalanced_payments"], "date_basis": "none"}},
    ]}), config=CompilerConfig(repair_catalog_errors=1))
    answer = agent.answer(question, Tools(con))
    assert answer.rows == [(1,)]
    assert "lane=typed" in answer.text


def test_fallback_runs_only_after_cannot_express_and_typed_only_abstains():
    con = stub()
    question = "How many current adjudications paid $0 but are not denials?"
    sql = "SELECT count(*) FROM sem.remit_claims WHERE is_current_adjudication AND paid_amount = 0 AND NOT is_denied"
    scripts = {question: [
        {"tool": "submit_spec", "input": {"intent": "cannot_express", "date_basis": "none",
                                          "reason": "paid_amount = 0 is a measure predicate"}},
        {"tool": "submit_sql", "input": {"sql": sql}},
    ]}
    gated = TypedCompilerAgent(llm=ScriptedLLM(scripts), config=CompilerConfig())
    # rebuild scripts because ScriptedLLM pops
    gated.llm = ScriptedLLM(scripts)
    answer = gated.answer(question, Tools(con))
    assert answer.rows == [(0,)]
    assert "fallback_sql" in answer.text
    abstain = TypedCompilerAgent(llm=ScriptedLLM(scripts), config=CompilerConfig(interface="typed_only", fallback="abstain"))
    answer = abstain.answer(question, Tools(con))
    assert answer.rows is None and not answer.refused
    assert answer.text.startswith("cannot_express")


def test_coverage_labels_for_the_shapes_the_compiler_owns():
    assert covers("SELECT count(DISTINCT claim_key) AS claims FROM sem.claims")["lane"] == "typed"
    assert covers("SELECT round(avg(is_denied::INT), 4) FROM sem.remit_claims WHERE is_current_adjudication")["expressible"]
    assert covers("SELECT count(*) FROM sem.payments")["expressible"]
    assert not covers("SELECT count(*) FROM sem.remit_claims WHERE is_current_adjudication AND paid_amount = 0 AND NOT is_denied")["expressible"]
    joined = ("SELECT a.carc_code, count(*) FROM sem.adjustments a JOIN sem.remit_claims r "
              "ON r.remit_claim_id = a.remit_claim_id GROUP BY 1")
    assert covers(joined)["lane"] == "fallback"
    cohort = ("SELECT count(*) FROM (SELECT claim_key, min(submission_date) AS first_submission FROM sem.claims "
              "GROUP BY 1) WHERE date_trunc('month', first_submission) = DATE '2026-07-01'")
    assert covers(cohort)["lane"] == "typed_cohort"
    assert covers("SELECT 1", expect_refusal=True)["lane"] == "refuse"
    assert covers("SELECT count(*) FROM lossless.edi_source_file", escalate=True)["expressible"] is False
