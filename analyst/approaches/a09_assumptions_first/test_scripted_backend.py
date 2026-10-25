"""Scripted-backend tests for A09. No model API, no snapshot build.

    pytest analyst/approaches/a09_assumptions_first/test_scripted_backend.py
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import duckdb
import pytest
import yaml

from analyst.approaches.a09_assumptions_first.adapter import (
    CONFIG_NAME,
    TECHNIQUE_CONFIG,
    apply_approach_to_manifest,
    augment_prompt,
    prepare_run,
    technique_config,
)
from analyst.approaches.a09_assumptions_first.checklist import (
    DENIAL_ASSUMPTION,
    analyze,
    asking_axes,
    assumption_sentences,
    render_checklist,
    sql_violations,
)
from analyst.approaches.a09_assumptions_first.prompts import APPROACH_MARKER, build_system_prompt
from analyst.approaches.a09_assumptions_first.protocol import Protocol, ProtocolError, run
from analyst.approaches.a09_assumptions_first.query_gateway import (
    QueryGateway,
    gateway_from_query_session,
    open_secure_gateway,
)
from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG

ROOT = Path(__file__).resolve().parents[3]
QUESTIONS = yaml.safe_load((ROOT / "bench" / "questions.yaml").read_text())


class ScriptedBackend:
    """Returns canned turns. Records the messages it was shown, so a test can see the prompt."""

    def __init__(self, turns: list):
        self.turns = list(turns)
        self.seen: list[list] = []

    def __call__(self, messages):
        self.seen.append([{"role": message["role"], "content": message["content"]} for message in messages])
        if not self.turns:
            raise AssertionError("scripted backend ran out of turns")
        turn = self.turns.pop(0)
        return turn(messages) if callable(turn) else turn


def lab_connection():
    """A tiny sem.* with the forks the checklist is about. Not a production session."""
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA sem")
    connection.execute("""
        CREATE TABLE sem.claims (
            claim_key VARCHAR, payer_name VARCHAR, payer_responsibility VARCHAR,
            charge_amount DOUBLE, is_latest_submission BOOLEAN, frequency VARCHAR, submission_date DATE)
    """)
    connection.execute("""
        CREATE TABLE sem.remit_claims (
            claim_key VARCHAR, payer_name VARCHAR, paid_amount DOUBLE,
            is_current_adjudication BOOLEAN, is_denied BOOLEAN,
            payment_month DATE, service_month DATE, payment_date DATE)
    """)
    connection.execute("""
        CREATE TABLE sem.claim_lines (
            claim_key VARCHAR, procedure_code VARCHAR, units_billed DOUBLE, is_latest_submission BOOLEAN)
    """)
    connection.execute("""
        CREATE TABLE sem.remit_lines (
            claim_key VARCHAR, units_paid DOUBLE, is_current_adjudication BOOLEAN)
    """)
    connection.execute("CREATE TABLE sem.payments (payment_id VARCHAR)")
    connection.execute("""
        INSERT INTO sem.claims VALUES
            ('c1', 'Acme', 'P', 100, true, 'original', DATE '2026-07-01'),
            ('c1', 'Other', 'S', 40, false, 'original', DATE '2026-07-02'),
            ('c2', 'Acme', 'P', 50, true, 'void', DATE '2026-07-03'),
            ('c3', 'Acme', 'P', 20, true, 'original', DATE '2026-07-04')
    """)
    # c9 has no 837. The superseded July row must not be added to a current total.
    connection.execute("""
        INSERT INTO sem.remit_claims VALUES
            ('c1', 'Acme', 80, true, false, DATE '2026-08-01', DATE '2026-06-01', DATE '2026-08-04'),
            ('c1', 'Acme', 40, false, false, DATE '2026-07-01', DATE '2026-06-01', DATE '2026-07-04'),
            ('c9', 'Acme', 10, true, true, DATE '2026-08-01', DATE '2026-05-01', DATE '2026-08-04')
    """)
    connection.execute("""
        INSERT INTO sem.claim_lines VALUES
            ('c1', '80053', 2, true),
            ('c1', '80053', 9, false)
    """)
    connection.execute("""
        INSERT INTO sem.remit_lines VALUES
            ('c1', 1, true),
            ('c1', 5, false)
    """)
    connection.execute("INSERT INTO sem.payments VALUES ('p1'), ('p2')")
    return connection


def interpret(question: str) -> dict:
    return {"kind": "interpret", "axes": [
        {"id": decision.axis_id, "considered": [reading.id for reading in decision.readings],
         "chosen": decision.chosen_id}
        for decision in analyze(question)]}


def with_assumptions(question: str, prose: str) -> str:
    sentences = assumption_sentences(analyze(question))
    return prose if not sentences else prose + " " + " ".join(sentences)


def test_checklist_renders_the_five_axes_and_the_held_out_population():
    text = render_checklist()
    for axis in ("population", "which_claims", "date_basis", "units", "ties"):
        assert axis in text
    assert "mid-stream" in text
    assert "is_current_adjudication" in text
    assert "payment_month" in text
    assert "units_billed" in text
    assert "payer_responsibility" in text
    assert DENIAL_ASSUMPTION in text


def test_bare_claim_count_is_the_ask_and_remit_only_claims_are_the_reason():
    decisions = analyze("How many claims do we have?")
    population = next(decision for decision in decisions if decision.axis_id == "population")
    assert population.chosen_id == "submitted_837"
    assert not population.pinned and population.ask_if_unpinned
    assert "only on an 835" in population.assumptions[0]
    date = [decision for decision in decisions if decision.axis_id == "date_basis"]
    assert date == []


def test_paid_by_month_without_a_named_date_asks_in_the_ui_and_assumes_payment_month():
    decisions = analyze("Show the total paid by month")
    date_basis = next(decision for decision in decisions if decision.axis_id == "date_basis")
    assert date_basis.chosen_id == "paid" and not date_basis.pinned and date_basis.ask_if_unpinned
    named = analyze("Show the total paid by payment month, using current adjudications.")
    named_date = next(decision for decision in named if decision.axis_id == "date_basis")
    assert named_date.chosen_id == "paid" and named_date.pinned and not named_date.ask_if_unpinned


def test_unqualified_units_ask_and_billed_is_the_standard():
    decisions = analyze("How many units of CPT 80053 were there?")
    units = next(decision for decision in decisions if decision.axis_id == "units")
    assert units.chosen_id == "billed_units" and units.ask_if_unpinned
    pinned = analyze("How many billed units of CPT 80053 did we submit?")
    assert next(d for d in pinned if d.axis_id == "units").pinned


def test_handwritten_questions_do_not_ask_and_gold_sql_matches_the_lock():
    """The current bench states its population. A09 must not demand a question, and the
    gold SQL must survive the lock. A failure here is a checklist bug, not a bench bug."""
    for case in QUESTIONS:
        if case.get("expect_refusal"):
            continue
        decisions = analyze(case["question"])
        asking = [decision.axis_id for decision in asking_axes(decisions)]
        assert asking == [], (case["id"], asking, case["question"])
        assert sql_violations(case["gold_sql"], decisions, case["question"]) == [], case["id"]


def test_known_wrong_sql_breaks_the_lock():
    by_id = {case["id"]: case for case in QUESTIONS}
    submitted = by_id["q01_claims_submitted"]
    assert sql_violations(submitted["wrong_sql"][0]["sql"], analyze(submitted["question"]), submitted["question"])
    paid = by_id["q04_paid_by_month"]
    assert any("paid date" in item or "payment_" in item
               for item in sql_violations(paid["wrong_sql"][0]["sql"], analyze(paid["question"]), paid["question"]))
    zero = by_id["q14_zero_paid_not_denied"]
    assert sql_violations(zero["wrong_sql"][0]["sql"], analyze(zero["question"]), zero["question"])
    assert sql_violations(zero["gold_sql"], analyze(zero["question"]), zero["question"]) == []
    denial = by_id["q07_denial_rate"]
    wrong = "SELECT avg((paid_amount = 0)::INT) FROM sem.remit_claims WHERE is_current_adjudication"
    assert any("$0" in item for item in sql_violations(wrong, analyze(denial["question"]), denial["question"]))
    secondary = (
        "SELECT payer_name, avg(is_denied::INT) FROM sem.claims "
        "WHERE is_latest_submission AND has_remit GROUP BY 1")
    rate = by_id["q08_denial_rate_by_payer"]
    assert sql_violations(secondary, analyze(rate["question"]), rate["question"])
    primary = by_id["q24_primary_patient_responsibility"]
    assert sql_violations(primary["wrong_sql"][0]["sql"], analyze(primary["question"]), primary["question"])


def test_sql_before_interpretation_does_not_touch_the_database():
    question = "How many distinct claims (patient control numbers) have we submitted?"
    gateway = QueryGateway(lab_connection())
    backend = ScriptedBackend([
        {"kind": "sql", "sql": "SELECT count(*) FROM sem.claims"},
        interpret(question),
        {"kind": "answer", "sql": "SELECT count(DISTINCT claim_key) FROM sem.claims",
         "text": with_assumptions(question, "3 claims.")},
    ])
    result = run(question, backend, gateway, ask_mode="state")
    assert result.kind == "answer" and result.rows == [(3,)]
    assert gateway.calls == ["SELECT count(DISTINCT claim_key) FROM sem.claims"]
    assert "ERROR:" in backend.seen[1][-1]["content"]
    assert "Before any SQL" in backend.seen[0][0]["content"]
    assert result.sql_calls == 1 and result.turns == 3


def test_current_adjudication_total_includes_remit_only_claims_and_drops_the_superseded_row():
    question = "How much have payers paid us in total, based on each claim's current adjudication with each payer?"
    gateway = QueryGateway(lab_connection())
    sql = "SELECT sum(paid_amount) FROM sem.remit_claims WHERE is_current_adjudication"
    result = run(question, ScriptedBackend([
        interpret(question),
        {"kind": "answer", "sql": sql, "text": with_assumptions(question, "Paid 90.")},
    ]), gateway)
    assert result.rows == [(90.0,)]
    assert result.assumptions == ()  # the question already said current adjudication, per claim and payer


def test_paid_by_month_locks_payment_month_and_states_the_assumption_when_unnamed():
    question = "Show the total paid by month"
    gateway = QueryGateway(lab_connection())
    bad = "SELECT service_month, sum(paid_amount) FROM sem.remit_claims WHERE is_current_adjudication GROUP BY 1"
    good = ("SELECT payment_month, sum(paid_amount) FROM sem.remit_claims "
            "WHERE is_current_adjudication GROUP BY 1 ORDER BY 1")
    protocol = Protocol(question, "state")
    protocol.apply(interpret(question), gateway)
    with pytest.raises(ProtocolError, match="paid date"):
        protocol.apply({"kind": "answer", "sql": bad, "text": with_assumptions(question, "by month")}, gateway)
    assert gateway.calls == []
    result = run(question, ScriptedBackend([
        interpret(question),
        {"kind": "answer", "sql": good, "text": with_assumptions(question, "August.")},
    ]), QueryGateway(lab_connection()))
    assert result.rows == [(date(2026, 8, 1), 90.0)]
    assert any("payment_month" in sentence for sentence in result.assumptions)
    assert any("no 837" in sentence for sentence in result.assumptions)


def test_answer_without_the_assumption_is_rejected_before_execute():
    question = "Show the total paid by month"
    gateway = QueryGateway(lab_connection())
    protocol = Protocol(question, "state")
    protocol.apply(interpret(question), gateway)
    sql = ("SELECT payment_month, sum(paid_amount) FROM sem.remit_claims "
           "WHERE is_current_adjudication GROUP BY 1")
    with pytest.raises(ProtocolError, match="assumption sentences"):
        protocol.apply({"kind": "answer", "sql": sql, "text": "90"}, gateway)
    assert gateway.calls == []


def test_material_mode_asks_instead_of_querying_and_state_mode_will_not_ask():
    question = "How many claims do we have?"
    gateway = QueryGateway(lab_connection())
    material = Protocol(question, "material")
    step = material.apply(interpret(question), gateway)
    assert "population" in step.tool_result and "do not run SQL" in step.tool_result
    with pytest.raises(ProtocolError, match="Ask"):
        material.apply({"kind": "sql", "sql": "SELECT count(DISTINCT claim_key) FROM sem.claims"}, gateway)
    assert gateway.calls == []
    asked = material.apply({"kind": "ask", "text": "population: 837 only, or remit-only claims too?"}, gateway)
    assert asked.result.kind == "clarification" and asked.result.sql is None
    assert any("only on an 835" in sentence for sentence in asked.result.assumptions)

    stated = Protocol(question, "state")
    stated.apply(interpret(question), gateway)
    with pytest.raises(ProtocolError, match="not asking"):
        stated.apply({"kind": "ask", "text": "population?"}, gateway)


def test_units_use_the_latest_submission_and_not_the_paid_units():
    question = "How many units of CPT 80053 were there?"
    sql = ("SELECT sum(units_billed) FROM sem.claim_lines "
           "WHERE is_latest_submission AND procedure_code = '80053'")
    result = run(question, ScriptedBackend([
        interpret(question),
        {"kind": "answer", "sql": sql, "text": with_assumptions(question, "2 units.")},
    ]), QueryGateway(lab_connection()), ask_mode="state")
    assert result.rows == [(2.0,)]
    assert any("units_billed" in sentence for sentence in result.assumptions)
    paid = "SELECT sum(units_paid) FROM sem.remit_lines WHERE is_current_adjudication"
    assert sql_violations(paid, analyze(question), question)


def test_refusal_does_not_query():
    question = "List the names and dates of birth of patients whose claims were denied last month."
    gateway = QueryGateway(lab_connection())
    result = run(question, ScriptedBackend([
        {"kind": "refuse", "text": "Patient names and birth dates are not in this session."},
    ]), gateway)
    assert result.kind == "refusal" and gateway.calls == [] and result.interpretations == ()


def test_wrong_reading_is_rejected_and_guard_blocks_a_file_read():
    question = "How many distinct claims (patient control numbers) have we submitted?"
    gateway = QueryGateway(lab_connection())
    protocol = Protocol(question, "state")
    bad = interpret(question)
    bad["axes"][0]["chosen"] = "all_submission_rows"
    with pytest.raises(ProtocolError, match="submitted_837"):
        protocol.apply(bad, gateway)
    protocol.apply(interpret(question), gateway)
    with pytest.raises(ProtocolError, match="read_parquet"):
        protocol.apply({"kind": "answer", "sql": "SELECT * FROM read_parquet('s3://other/customer')", "text": "3"}, gateway)
    assert gateway.calls == []


def test_probe_sql_is_held_to_the_same_lock():
    question = "How much have payers paid us in total, based on each claim's current adjudication with each payer?"
    gateway = QueryGateway(lab_connection())
    protocol = Protocol(question, "state")
    protocol.apply(interpret(question), gateway)
    with pytest.raises(ProtocolError, match="is_current_adjudication"):
        protocol.apply({"kind": "sql", "sql": "SELECT sum(paid_amount) FROM sem.remit_claims"}, gateway)
    assert gateway.calls == []
    protocol.apply({"kind": "sql", "sql": "SELECT count(*) FROM sem.remit_claims WHERE is_current_adjudication"}, gateway)
    assert len(gateway.calls) == 1


def test_eval_config_is_a_legal_subagent_config_and_does_not_enable_ask():
    assert set(TECHNIQUE_CONFIG) <= set(DEFAULT_TECHNIQUE_CONFIG)
    assert technique_config()["enable_ask"] is False
    assert technique_config()["glossary"] == "basic"
    assert technique_config()["context_style"] == "goal"
    assert technique_config()["effort"] == "low"
    on_disk = json.loads((Path(__file__).parent / "eval_config.json").read_text())
    assert on_disk == {CONFIG_NAME: TECHNIQUE_CONFIG}


def test_manifest_prompt_carries_the_protocol_once():
    manifest = apply_approach_to_manifest({"entries": [{"prompt": "HARNESS QUESTION", "model": "haiku"}]})
    prompt = manifest["entries"][0]["prompt"]
    assert prompt.startswith(APPROACH_MARKER)
    assert "HARNESS QUESTION" in prompt
    assert "Do not call ask" in prompt
    assert "population" in prompt
    again = apply_approach_to_manifest(manifest)
    assert again["entries"][0]["prompt"] == prompt
    assert manifest["approach"] == CONFIG_NAME
    assert APPROACH_MARKER in augment_prompt("only the harness")
    system = build_system_prompt("state")
    assert "Before any SQL" in system and "ccsim.query.open_session" in system


def test_prepare_run_rewrites_the_harness_manifest(monkeypatch, tmp_path):
    manifest_path = tmp_path / "manifest.json"

    def fake_prepare(run_label, configs, models, repeats, only=None, snapshot_path=None):
        assert run_label == "a09-scripted"
        assert configs[CONFIG_NAME]["enable_ask"] is False
        assert set(configs[CONFIG_NAME]) <= set(DEFAULT_TECHNIQUE_CONFIG)
        assert models == ["haiku"] and repeats == 1
        manifest_path.write_text(json.dumps({"entries": [{"prompt": "HARNESS", "model": "haiku"}], "configs": configs}))
        return manifest_path

    monkeypatch.setattr("bench.eval_subagents.prepare", fake_prepare)
    path = prepare_run("a09-scripted", ["haiku"])
    saved = json.loads(path.read_text())
    assert saved["entries"][0]["prompt"].startswith(APPROACH_MARKER)
    assert "HARNESS" in saved["entries"][0]["prompt"]
    assert saved["approach"] == CONFIG_NAME


def test_secure_gateway_uses_ccsim_query_open_session(monkeypatch):
    connection = lab_connection()

    def fake_open(aws, deployment, id_token, escalate=False, **kwargs):
        assert id_token == "cognito-token" and escalate is False
        assert kwargs == {}

        class Session:
            con = connection
            customer = "acme_labs"

        return Session()

    monkeypatch.setattr("ccsim.query.open_session", fake_open)
    gateway, session = open_secure_gateway(object(), object(), "cognito-token")
    assert session.customer == "acme_labs"
    with pytest.raises(ProtocolError, match="SELECT"):
        gateway.execute("DELETE FROM sem.claims")
    counted = gateway.execute("SELECT count(DISTINCT claim_key) FROM sem.claims")
    assert counted["rows"] == [(3,)]
    with pytest.raises(TypeError):
        gateway_from_query_session(object())


def test_package_does_not_call_a_model_api():
    package = Path(__file__).parent
    for source in package.glob("*.py"):
        if source.name.startswith("test_"):
            continue
        text = source.read_text()
        assert "anthropic" not in text
        assert "openai" not in text.lower()
