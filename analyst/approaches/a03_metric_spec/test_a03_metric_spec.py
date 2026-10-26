"""Compiler, coverage, and eval-adapter tests for A03. No model calls."""
from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import pytest

from analyst.approaches.a03_metric_spec.adapter import (
    build_prompt,
    estimate_question_cost,
    main,
    prefix_tokens,
    sql_fallback_allowed,
    technique_config,
)
from analyst.approaches.a03_metric_spec.catalog import POPULATIONS
from analyst.approaches.a03_metric_spec.compiler import CannotExpress, SpecError, compile_spec
from analyst.approaches.a03_metric_spec.coverage import (
    HANDWRITTEN_SPECS,
    REVIEWED_QUESTION_SETS,
    classify_gold_sql,
    coverage_report,
)
from analyst.approaches.a03_metric_spec.schema import tool_input_schema
from bench.guard import guard

ROOT = Path(__file__).resolve().parents[3]
VIEW_SQL = (ROOT / "claude/schema/02_semantic_layer_views.sql").read_text()


def _view_body(view: str) -> str:
    marker = f"CREATE OR REPLACE VIEW {view} AS"
    start = VIEW_SQL.index(marker) + len(marker)
    rest = VIEW_SQL[start:]
    nxt = rest.find("\nCREATE OR REPLACE VIEW ")
    return rest if nxt < 0 else rest[:nxt]


def test_catalog_columns_are_real_view_columns():
    for pop in POPULATIONS.values():
        skip = set(pop.derived_columns) | {name for name, _expr in pop.virtual}
        blob = "\n".join(_view_body(view) for view in pop.source_views)
        for column in pop.columns:
            if column in skip:
                continue
            assert re.search(rf"\b{re.escape(column)}\b", blob), (pop.name, column)


def test_labeled_specs_compile_and_match_the_coverage_counts():
    report = coverage_report()
    assert report["handwritten"]["expressible"] == 28
    assert report["handwritten"]["refusal"] == 4
    assert report["handwritten"]["escalation"] == 1
    assert report["handwritten"]["expressible_of_non_refusal"] == [28, 29]
    assert report["families"] == {"templates": 16, "expressible": 16}
    assert report["authored"]["expressible"] == 8
    assert report["authored"]["questions"] == 66
    assert report["authored"]["heldout_hard_1"]["expressible"] == 1
    assert report["authored"]["heldout_hard_1"]["questions"] == 27
    assert report["authored"]["screen_authored"]["expressible"] == 7
    for spec in HANDWRITTEN_SPECS.values():
        guarded = guard(compile_spec(spec).sql, 1000, False)
        assert "sem." in guarded
        assert "lossless" not in guarded.lower()


def test_tool_schema_enums_match_the_catalog():
    schema = tool_input_schema()
    assert set(schema["properties"]["population"]["enum"]) == set(POPULATIONS)
    assert "adjudication_adjustments" in schema["properties"]["link"]["enum"]


def test_technique_config_is_legal_for_eval_subagents():
    config = technique_config()
    assert config["enable_metrics"] is False
    assert config["enable_sql"] is True
    assert config["context_style"] == "goal"
    assert config["effort"] == "low"
    assert config["glossary"] == "basic"


def test_prompt_requires_spec_before_sql_and_names_the_question():
    text = build_prompt("How many claims were denied?", "/tmp/session", escalate=False)
    assert "How many claims were denied?" in text
    assert "cannot_express" in text
    assert "bench.agent_cli" not in text
    assert "compile_metric_spec" not in text or "a03_metric_spec" in text
    assert "patient names" in text
    cold = estimate_question_cost("sonnet", prefix_tokens(), cache_warm=False, sql_fallback=False)
    warm = estimate_question_cost("haiku", prefix_tokens(), cache_warm=True, sql_fallback=False)
    fallback = estimate_question_cost("haiku", prefix_tokens(), cache_warm=True, sql_fallback=True)
    assert cold > warm
    assert fallback > warm
    assert prefix_tokens() > 1000


def test_shape_errors_do_not_open_the_sql_fallback():
    with pytest.raises(SpecError):
        compile_spec({"population": "current_adjudications", "metrics": [{"alias": "n", "agg": "count"}], "window": {}})
    assert not sql_fallback_allowed([{"command": "metrics", "ok": False, "result": "spec: unexpected keys"}])
    assert sql_fallback_allowed([{
        "command": "metrics", "ok": False, "result": "cannot_express: needs a 7-day rolling sum",
    }])
    with pytest.raises(CannotExpress, match="rolling"):
        compile_spec({"status": "cannot_express", "because": "needs a 7-day rolling sum"})


def test_compiler_rejects_fanout_unknown_fields_and_median():
    with pytest.raises(CannotExpress, match="repeat once per adjustment"):
        compile_spec({
            "population": "current_adjudications",
            "link": "adjudication_adjustments",
            "metrics": [{"alias": "paid", "agg": "sum", "field": "paid_amount"}],
        })
    with pytest.raises(CannotExpress, match="not on this population"):
        compile_spec({
            "population": "current_adjudications",
            "metrics": [{"alias": "n", "agg": "count_distinct", "field": "patient_name"}],
        })
    with pytest.raises(CannotExpress, match="not in the spec"):
        compile_spec({
            "population": "current_adjudications",
            "metrics": [{"alias": "m", "agg": "median", "field": "paid_amount"}],
        })
    with pytest.raises(CannotExpress, match="does not apply"):
        compile_spec({
            "population": "payments",
            "link": "billed_procedures",
            "metrics": [{"alias": "n", "agg": "count"}],
        })
    with pytest.raises(CannotExpress, match="unknown population"):
        compile_spec({"population": "lossless_files", "metrics": [{"alias": "n", "agg": "count"}]})


def test_filter_value_is_a_literal_not_sql():
    compiled = compile_spec({
        "population": "current_adjudications",
        "filters": [{"field": "payer_name", "op": "=", "value": "x' OR 1=1 --"}],
        "metrics": [{"alias": "n", "agg": "count"}],
    })
    assert "x'' OR 1=1 --" in compiled.sql
    assert compiled.sql.lower().count("select") == 1
    guard(compiled.sql, 1000, False)


def _fixture() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute("CREATE SCHEMA sem")
    con.execute("""
        CREATE TABLE sem.claims (
            claim_key VARCHAR, is_latest_submission BOOLEAN, frequency_code VARCHAR,
            payer_key VARCHAR, payer_name VARCHAR, payer_responsibility VARCHAR,
            charge_amount DOUBLE, has_remit BOOLEAN, referring_npi VARCHAR,
            principal_diagnosis_code VARCHAR, submission_date DATE)
    """)
    con.execute("""
        CREATE TABLE sem.remit_claims (
            remit_claim_id VARCHAR, claim_key VARCHAR, claim_payer_key VARCHAR,
            payer_key VARCHAR, payer_name VARCHAR, payer_class VARCHAR, payer_sequence VARCHAR,
            is_current_adjudication BOOLEAN, is_reversal BOOLEAN, is_denied BOOLEAN,
            is_denied_recorded BOOLEAN, is_denied_signal_v3 BOOLEAN,
            charge_amount DOUBLE, paid_amount DOUBLE, patient_responsibility_amount DOUBLE,
            payment_date DATE, payment_month DATE)
    """)
    con.execute("""
        CREATE TABLE sem.claim_lines (
            claim_key VARCHAR, is_latest_submission BOOLEAN, procedure_code VARCHAR,
            modifier_1 VARCHAR, modifier_2 VARCHAR, modifier_3 VARCHAR, modifier_4 VARCHAR)
    """)
    con.execute("""
        CREATE TABLE sem.remit_lines (
            claim_key VARCHAR, payer_name VARCHAR, is_current_adjudication BOOLEAN,
            claim_is_denied BOOLEAN, line_is_denied BOOLEAN, procedure_code VARCHAR,
            line_paid_amount DOUBLE, line_charge_amount DOUBLE)
    """)
    con.execute("""
        CREATE TABLE sem.adjustments (
            remit_claim_id VARCHAR, claim_key VARCHAR, payer_key VARCHAR,
            is_current_adjudication BOOLEAN, group_code VARCHAR, carc_code VARCHAR,
            is_denial_carc BOOLEAN, adjustment_amount DOUBLE)
    """)
    con.execute("CREATE TABLE sem.payments (payment_id VARCHAR, is_balanced BOOLEAN)")
    con.execute("""
        CREATE TABLE sem.provider_adjustments (
            plb_reason_code VARCHAR, provider_adjustment_amount DOUBLE)
    """)
    con.executemany("INSERT INTO sem.claims VALUES (?,?,?,?,?,?,?,?,?,?,?)", [
        ("A", False, "1", "PK1", "Acme Health", "P", 80, True, "NPI1", "DX1", "2024-01-01"),
        ("A", True, "7", "PK1", "Acme Health", "P", 100, True, "NPI1", "DX1", "2024-01-20"),
        ("B", False, "7", "PK1", "Acme Health", "P", 10, True, "NPI2", "DX2", "2024-01-15"),
        ("B", True, "1", "PK1", "Acme Health", "P", 50, True, "NPI2", "DX2", "2024-02-01"),
        ("C", True, "1", "PK2", "Beta Medicaid", "S", 40, True, "NPI9", "DX9", "2024-02-10"),
        ("D", True, "8", "PK1", "Acme Health", "P", 70, True, "NPI1", "DX1", "2024-03-01"),
        ("E", True, "1", "PK1", "Acme Health", "P", 20, False, "NPI2", "DX2", "2024-03-15"),
        ("F", True, "1", "PK2", "Beta Medicaid", "P", 30, True, "NPI3", "DX1", "2024-01-05"),
    ])
    con.executemany("INSERT INTO sem.remit_claims VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", [
        ("R0", "A", "A|PK1", "PK1", "Acme Health", "commercial", "unknown", False, True, False, False, False, 80, -80, 0, "2024-01-18", "2024-01-01"),
        ("R1", "A", "A|PK1", "PK1", "Acme Health", "commercial", "primary", True, False, False, False, False, 100, 60, 25, "2024-02-02", "2024-02-01"),
        ("R2", "A", "A|PK2", "PK2", "Beta Medicaid", "medicaid", "secondary", True, False, False, False, False, 100, 15, 5, "2024-02-20", "2024-02-01"),
        ("R3", "B", "B|PK1", "PK1", "Acme Health", "commercial", "unknown", True, False, True, True, False, 50, 0, 0, "2024-02-15", "2024-02-01"),
        ("R4", "C", "C|PK2", "PK2", "Beta Medicaid", "medicaid", "secondary", True, False, False, False, False, 40, 10, 8, "2024-03-01", "2024-03-01"),
        ("R5", "D", "D|PK1", "PK1", "Acme Health", "commercial", "unknown", True, False, True, False, True, 70, 0, 0, "2024-03-10", "2024-03-01"),
        ("R6", "F", "F|PK2", "PK2", "Beta Medicaid", "medicaid", "primary", True, False, False, False, False, 30, 0, 30, "2024-01-28", "2024-01-01"),
        ("R7", "G", "G|PK1", "PK1", "Acme Health", "commercial", "unknown", False, True, False, False, False, 10, -10, 0, "2024-04-01", "2024-04-01"),
        ("R8", "H", "H|PK1", "PK1", "Acme Health", "commercial", "unknown", True, False, True, True, False, 40, 0, 0, "2024-03-05", "2024-03-01"),
        ("R9", "I", "I|PK3", "PK3", "Other Payer", "commercial", "unknown", False, True, False, False, False, 5, -5, 0, "2024-04-02", "2024-04-01"),
    ])
    con.executemany("INSERT INTO sem.claim_lines VALUES (?,?,?,?,?,?,?)", [
        ("A", True, "80053", "59", None, None, None),
        ("A", True, "85025", None, None, None, None),
        ("A", False, "99999", None, None, None, None),
        ("B", True, "80053", "26", None, None, None),
        ("B", False, "80053", "59", None, None, None),
        ("F", True, "81001", None, None, None, None),
    ])
    con.executemany("INSERT INTO sem.remit_lines VALUES (?,?,?,?,?,?,?,?)", [
        ("A", "Acme Health", True, False, True, "80053", 0, 40),
        ("A", "Acme Health", True, False, False, "85025", 60, 60),
        ("F", "Beta Medicaid", True, False, False, "81001", 40, 30),
    ])
    con.executemany("INSERT INTO sem.adjustments VALUES (?,?,?,?,?,?,?,?)", [
        ("R3", "B", "PK1", True, "CO", "16", True, 50),
        ("R3", "B", "PK1", True, "CO", "16", True, 10),
        ("R5", "D", "PK1", True, "CO", "16", True, 20),
        ("R5", "D", "PK1", True, "CO", "50", True, 70),
        ("R8", "H", "PK1", True, "CO", "29", True, 40),
        ("R8", "H", "PK1", True, "CO", "45", False, 5),
        ("R1", "A", "PK1", True, "PR", "1", False, 12),
        ("R2", "A", "PK2", True, "PR", "1", False, 5),
        ("R6", "F", "PK2", True, "PR", "1", False, 30),
        ("R0", "A", "PK1", False, "CO", "29", True, 80),
    ])
    con.executemany("INSERT INTO sem.payments VALUES (?,?)", [("P1", True), ("P2", False)])
    con.executemany(
        "INSERT INTO sem.provider_adjustments VALUES (?,?)", [("L6", -3.25), ("WO", 10.0)])
    return con


def _same(con, gold: str, spec: dict, ordered: bool = False):
    compiled = compile_spec(spec).sql
    expect = con.execute(gold).fetchall()
    got = con.execute(compiled).fetchall()
    if not ordered:
        expect = sorted(expect, key=lambda row: tuple(str(cell) for cell in row))
        got = sorted(got, key=lambda row: tuple(str(cell) for cell in row))
    assert got == expect, (expect, got, compiled)


def test_compiled_sql_matches_gold_on_a_fixture():
    con = _fixture()
    _same(con, "SELECT count(DISTINCT claim_key) FROM sem.claims", HANDWRITTEN_SPECS["q01_claims_submitted"])
    _same(con, "SELECT round(sum(charge_amount), 2) FROM sem.claims WHERE is_latest_submission",
          HANDWRITTEN_SPECS["q02_total_billed"])
    _same(con, "SELECT round(sum(paid_amount), 2) FROM sem.remit_claims WHERE is_current_adjudication",
          HANDWRITTEN_SPECS["q03_total_paid"], ordered=True)
    _same(con,
          "SELECT payment_month, round(sum(paid_amount), 2) FROM sem.remit_claims "
          "WHERE is_current_adjudication GROUP BY 1 ORDER BY 1",
          HANDWRITTEN_SPECS["q04_paid_by_month"], ordered=True)
    _same(con, "SELECT coalesce(round(-sum(provider_adjustment_amount), 2), 0) FROM sem.provider_adjustments "
          "WHERE plb_reason_code = 'L6'", HANDWRITTEN_SPECS["q06_interest_paid"])
    _same(con, "SELECT round(avg(is_denied::INT), 4) FROM sem.remit_claims WHERE is_current_adjudication",
          HANDWRITTEN_SPECS["q07_denial_rate"])
    _same(con, "SELECT payer_name, round(avg(is_denied::INT), 4) FROM sem.remit_claims "
          "WHERE is_current_adjudication GROUP BY 1 ORDER BY 2 DESC",
          HANDWRITTEN_SPECS["q08_denial_rate_by_payer"], ordered=True)
    _same(con,
          "SELECT count(*) FILTER (WHERE is_denied_recorded), "
          "count(*) FILTER (WHERE is_denied_signal_v3 AND NOT is_denied_recorded) "
          "FROM sem.remit_claims WHERE is_current_adjudication",
          HANDWRITTEN_SPECS["q09_denials_by_type"])
    _same(con,
          "SELECT a.carc_code, count(DISTINCT a.remit_claim_id) FROM sem.adjustments a "
          "JOIN sem.remit_claims r ON r.remit_claim_id = a.remit_claim_id "
          "WHERE r.is_current_adjudication AND r.is_denied AND a.group_code = 'CO' AND a.is_denial_carc "
          "GROUP BY 1 ORDER BY 2 DESC, 1",
          HANDWRITTEN_SPECS["q10_top_denial_carcs"], ordered=True)
    cpt = json.loads(json.dumps(HANDWRITTEN_SPECS["q11_cpt_denial_rate"]))
    cpt["having"] = [{"alias": "adjudications", "op": ">=", "value": 1}]
    cpt["order_by"] = [
        {"field": "denial_rate", "direction": "desc"},
        {"field": "procedure_code", "direction": "asc"},
    ]
    _same(con,
          "WITH lines AS (SELECT DISTINCT claim_key, procedure_code FROM sem.claim_lines WHERE is_latest_submission) "
          "SELECT l.procedure_code, round(avg(r.is_denied::INT), 4), count(*) "
          "FROM lines l JOIN sem.remit_claims r ON r.claim_key = l.claim_key AND r.is_current_adjudication "
          "GROUP BY 1 HAVING count(*) >= 1 ORDER BY 2 DESC, 1",
          cpt, ordered=True)
    assert ">= 20" in compile_spec(HANDWRITTEN_SPECS["q11_cpt_denial_rate"]).sql
    referring = json.loads(json.dumps(HANDWRITTEN_SPECS["q12_referring_provider_denials"]))
    referring["having"] = [{"alias": "claims", "op": ">=", "value": 1}]
    referring["limit"] = 5
    _same(con,
          "WITH c AS (SELECT DISTINCT claim_key, referring_npi, payer_key FROM sem.claims WHERE payer_responsibility = 'P') "
          "SELECT c.referring_npi, round(avg(r.is_denied::INT), 4), count(*) FROM c "
          "JOIN sem.remit_claims r ON r.claim_key = c.claim_key AND r.payer_key = c.payer_key "
          "AND r.is_current_adjudication GROUP BY 1 HAVING count(*) >= 1 ORDER BY 2 DESC, 1 LIMIT 5",
          referring, ordered=True)
    _same(con, "SELECT count(*) FROM sem.remit_claims WHERE is_current_adjudication AND paid_amount = 0 AND NOT is_denied",
          HANDWRITTEN_SPECS["q14_zero_paid_not_denied"])
    _same(con, "SELECT count(*) FROM sem.remit_lines WHERE is_current_adjudication AND line_is_denied AND NOT claim_is_denied",
          HANDWRITTEN_SPECS["q15_partial_line_denials"])
    _same(con, "SELECT count(DISTINCT claim_key) FROM sem.claims WHERE frequency_code = '7'",
          HANDWRITTEN_SPECS["q17_resubmitted"])
    _same(con,
          "SELECT count(*) FROM (SELECT claim_payer_key FROM sem.remit_claims GROUP BY 1 "
          "HAVING bool_or(is_reversal) AND NOT bool_or(is_current_adjudication))",
          HANDWRITTEN_SPECS["q19_reversed_no_correction"])
    _same(con, "SELECT count(DISTINCT claim_key) FROM sem.claims WHERE NOT has_remit",
          HANDWRITTEN_SPECS["q20_never_remitted"])
    _same(con,
          "SELECT count(DISTINCT r.remit_claim_id) FROM sem.remit_claims r "
          "JOIN sem.adjustments a ON a.remit_claim_id = r.remit_claim_id "
          "WHERE r.is_current_adjudication AND r.is_denied AND a.carc_code = '29' AND a.group_code = 'CO'",
          HANDWRITTEN_SPECS["q21_timely_filing"])
    _same(con,
          "WITH s AS (SELECT claim_key, payer_key, min(submission_date) AS first_sub FROM sem.claims "
          "WHERE payer_responsibility = 'P' GROUP BY 1, 2), "
          "r AS (SELECT claim_key, payer_key, min(payer_name) AS payer_name, min(payment_date) AS first_remit "
          "FROM sem.remit_claims GROUP BY 1, 2) "
          "SELECT r.payer_name, round(avg(date_diff('day', s.first_sub, r.first_remit)), 1) "
          "FROM s JOIN r USING (claim_key, payer_key) GROUP BY 1 ORDER BY 2 DESC",
          HANDWRITTEN_SPECS["q22_days_to_first_remit"], ordered=True)
    _same(con,
          "SELECT count(*) FILTER (WHERE paid_amount > 0), coalesce(round(sum(paid_amount), 2), 0) "
          "FROM sem.remit_claims WHERE is_current_adjudication AND payer_sequence = 'secondary'",
          HANDWRITTEN_SPECS["q23_secondary_paid"])
    _same(con,
          "WITH p AS (SELECT DISTINCT claim_key, payer_key FROM sem.claims WHERE payer_responsibility = 'P') "
          "SELECT coalesce(round(sum(r.patient_responsibility_amount), 2), 0) FROM sem.remit_claims r "
          "JOIN p USING (claim_key, payer_key) WHERE r.is_current_adjudication",
          HANDWRITTEN_SPECS["q24_primary_patient_responsibility"])
    _same(con,
          "WITH p AS (SELECT DISTINCT claim_key, payer_key FROM sem.claims WHERE payer_responsibility = 'P') "
          "SELECT coalesce(round(sum(a.adjustment_amount), 2), 0) FROM sem.adjustments a "
          "JOIN p USING (claim_key, payer_key) WHERE a.is_current_adjudication AND a.group_code = 'PR' AND a.carc_code = '1'",
          HANDWRITTEN_SPECS["q25_primary_deductible"])
    _same(con, "SELECT count(DISTINCT payer_key) FROM sem.remit_claims", HANDWRITTEN_SPECS["q26_payers_remitting"])
    _same(con, "SELECT count(*) FROM sem.payments WHERE NOT is_balanced", HANDWRITTEN_SPECS["q27_unbalanced_payments"])
    _same(con,
          "SELECT count(DISTINCT claim_key) FROM sem.claim_lines WHERE is_latest_submission "
          "AND '59' IN (modifier_1, modifier_2, modifier_3, modifier_4)",
          HANDWRITTEN_SPECS["q28_modifier_59"])
    _same(con,
          "SELECT round(sum(line_paid_amount) FILTER (WHERE procedure_code = '85025') / sum(line_paid_amount), 4) "
          "FROM sem.remit_lines WHERE is_current_adjudication",
          {
              "population": "current_service_lines",
              "metrics": [{
                  "alias": "share", "agg": "ratio",
                  "numerator": {"agg": "sum", "field": "line_paid_amount",
                                "where": [{"field": "procedure_code", "op": "=", "value": "85025"}]},
                  "denominator": {"agg": "sum", "field": "line_paid_amount"},
              }],
              "rounding": {"digits": 4},
          })
    _same(con,
          "SELECT coalesce(round(sum(r.paid_amount), 2), 0) FROM sem.remit_claims r "
          "JOIN (SELECT DISTINCT claim_key FROM sem.claims WHERE payer_responsibility = 'P' AND payer_name = 'Acme Health') p "
          "USING (claim_key) WHERE r.is_current_adjudication AND r.payer_sequence = 'secondary'",
          {
              "population": "secondary_current_for_primary",
              "filters": [{"field": "primary_payer_name", "op": "=", "value": "Acme Health"}],
              "metrics": [{"alias": "paid", "agg": "sum", "field": "paid_amount"}],
              "rounding": {"digits": 2},
          })
    empty = con.execute(compile_spec({
        "population": "current_adjudications",
        "filters": [{"field": "payer_name", "op": "=", "value": "nobody"}],
        "metrics": [{"alias": "paid", "agg": "sum", "field": "paid_amount"}],
        "rounding": {"digits": 2},
    }).sql).fetchall()
    assert empty == [(0,)]
    con.execute(compile_spec({
        "population": "current_adjudications",
        "filters": [{"field": "payer_name", "op": "=", "value": "x' OR 1=1 --"}],
        "metrics": [{"alias": "n", "agg": "count"}],
    }).sql)
    assert con.fetchall() == [(0,)]


def test_include_boundary_keeps_ties_and_strict_does_not():
    con = _fixture()
    base = {
        "population": "current_adjudications",
        "metrics": [{"alias": "paid", "agg": "sum", "field": "paid_amount"}],
        "grain": ["payer_name"],
        "order_by": [{"field": "paid", "direction": "desc"}, {"field": "payer_name", "direction": "asc"}],
        "limit": 1,
    }
    # Give the two payers the same total so the tie is the whole result. Acme paid 60, Beta paid 25.
    # Force a tie by reading a population of three equal groups from a one-off spec on payments is awkward.
    # Use remit events of payers whose current paid we overwrite conceptually via filters that cannot tie.
    # Build the tie on provider adjustments instead: three reasons with the same flipped amount.
    con.execute("DELETE FROM sem.provider_adjustments")
    con.executemany("INSERT INTO sem.provider_adjustments VALUES (?,?)", [("AA", 5.0), ("BB", 5.0), ("CC", 1.0)])
    spec = {
        "population": "provider_adjustments",
        "metrics": [{"alias": "amount", "agg": "sum", "field": "provider_adjustment_amount"}],
        "grain": ["plb_reason_code"],
        "order_by": [{"field": "amount", "direction": "desc"}, {"field": "plb_reason_code", "direction": "asc"}],
        "limit": 1,
    }
    strict = con.execute(compile_spec({**spec, "ties": "strict"}).sql).fetchall()
    tied = con.execute(compile_spec({**spec, "ties": "include_boundary"}).sql).fetchall()
    assert strict == [("AA", 5.0)]
    assert tied == [("AA", 5.0), ("BB", 5.0)]
    del base


def test_cli_refuses_sql_until_the_spec_cannot_express(tmp_path):
    session = tmp_path / "session"
    session.mkdir()
    (session / "session.json").write_text(json.dumps({
        "question": "How many?", "technique_config": technique_config(),
        "snapshot_path": "unused", "escalate": False,
    }))
    assert main(["--session", str(session), "answer", "--sql", "SELECT count(*) FROM sem.claims", "--text", "n"]) == 1
    assert not (session / "final.json").exists()
    (session / "calls.jsonl").write_text(json.dumps({
        "command": "metrics", "ok": False, "result": "cannot_express: needs a rolling window",
    }) + "\n")
    assert main(["--session", str(session), "answer", "--sql", "SELECT count(*) FROM sem.claims", "--text", "n"]) == 0
    final = json.loads((session / "final.json").read_text())
    assert final["via"] == "sql_fallback"
    assert final["kind"] == "answer"
    other = tmp_path / "other"
    other.mkdir()
    (other / "session.json").write_text((session / "session.json").read_text())
    assert main(["--session", str(other), "refuse", "patient names are not in the session"]) == 0
    assert json.loads((other / "final.json").read_text())["kind"] == "refusal"


def test_authored_expressible_ids_stay_reviewed():
    import json as jsonlib
    from pathlib import Path as Pathlib
    authored = jsonlib.loads((ROOT / "bench/authored_questions.json").read_text())
    authored = [q for q in authored if q.get("question_set") in REVIEWED_QUESTION_SETS]
    expressible = {q["id"] for q in authored if classify_gold_sql(q["gold_sql"]) != "outside_spec"
                   and classify_gold_sql(q["gold_sql"]) != "escalation"}
    assert expressible == {
        "a_54dd76175a", "a_436f690741", "a_f494340e13", "a_dccab5ccfe",
        "a_fca405b635", "a_26d4c3f4ca", "a_e83408d6a2", "a_8edecbf01f",
    }
    del Pathlib
