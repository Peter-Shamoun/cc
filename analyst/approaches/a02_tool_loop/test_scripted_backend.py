"""Scripted-backend tests for A02. No model API and no lake snapshot."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import duckdb
import pytest

from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
from bench.agent_session import SESSIONS_ROOT, create_session
from bench.agents import Tools

from analyst.approaches.a02_tool_loop.adapter import (
    CONFIG_NAME,
    CONFIG_PATH,
    A02ToolLoopAgent,
    ToolsExecutor,
    configs_document,
    drive_manifest,
    drive_session,
    main,
)
from analyst.approaches.a02_tool_loop.backend import ScriptExhausted, ScriptedBackend
from analyst.approaches.a02_tool_loop.checks import suspicious_result_signals
from analyst.approaches.a02_tool_loop.protocol import (
    MAX_REPAIRS,
    classify_execution,
    documented_example_costs,
    parse_schema_catalog,
    run_question,
    ExecutionResult,
)
from analyst.approaches.a02_tool_loop.tool_spec import TOOL_NAMES

LATEST_COUNT = (
    "SELECT count(DISTINCT claim_key) AS claims FROM sem.claims WHERE is_latest_submission"
)


def make_connection() -> duckdb.DuckDBPyConnection:
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA sem")
    connection.execute("""
        CREATE TABLE backing_claims (
            claim_key VARCHAR,
            is_latest_submission BOOLEAN,
            payer_responsibility VARCHAR,
            charge_amount DOUBLE
        )
    """)
    connection.execute("""
        INSERT INTO backing_claims VALUES
        ('c1', true, 'P', 100.0),
        ('c1', false, 'P', 90.0),
        ('c2', true, 'S', 50.0),
        ('c3', false, 'P', 40.0)
    """)
    connection.execute("CREATE VIEW sem.claims AS SELECT * FROM backing_claims")
    connection.execute("""
        CREATE TABLE backing_remit (
            claim_key VARCHAR,
            payer_name VARCHAR,
            is_current_adjudication BOOLEAN,
            paid_amount DOUBLE
        )
    """)
    connection.execute("""
        INSERT INTO backing_remit VALUES
        ('c1', 'Aetna', true, 80.0),
        ('c1', 'Aetna', false, 0.0),
        ('c2', 'Beta', true, 0.0)
    """)
    connection.execute("CREATE VIEW sem.remit_claims AS SELECT * FROM backing_remit")
    connection.execute("CREATE TABLE backing_payers (payer_name VARCHAR)")
    connection.execute("CREATE VIEW sem.payers AS SELECT * FROM backing_payers")
    return connection


class CountingExecutor(ToolsExecutor):
    def __init__(self, tools):
        super().__init__(tools)
        self.sql_texts: list[str] = []

    def run_sql(self, sql: str) -> ExecutionResult:
        self.sql_texts.append(sql)
        return super().run_sql(sql)


def run_script(connection, script, question="How many distinct claims are on their latest submission?"):
    tools = Tools(connection)
    executor = CountingExecutor(tools)
    backend = ScriptedBackend(script)
    answer = run_question(question, executor, backend)
    return answer, executor, backend


def step(tool, **arguments):
    return {"tool": tool, "arguments": arguments}


def test_schema_catalog_parses_decimal_commas():
    text = "sem.claims(claim_key VARCHAR, charge_amount DECIMAL(18,2), is_latest_submission BOOLEAN)"
    assert parse_schema_catalog(text) == {
        "sem.claims": ["claim_key", "charge_amount", "is_latest_submission"],
    }


def test_suspicion_signals_are_specific():
    assert suspicious_result_signals(
        "SELECT sum(charge_amount) FROM sem.claims WHERE is_latest_submission",
        ["sum(charge_amount)"], [(150.0,)],
    ) == []
    missing_filter = suspicious_result_signals(
        "SELECT sum(charge_amount) FROM sem.claims -- is_latest_submission\nWHERE true",
        ["sum(charge_amount)"], [(280.0,)],
    )
    assert any("is_latest_submission" in signal for signal in missing_filter)
    hidden_in_literal = suspicious_result_signals(
        "SELECT sum(charge_amount) FROM sem.claims WHERE note = 'is_latest_submission'",
        ["sum(charge_amount)"], [(280.0,)],
    )
    assert any("is_latest_submission" in signal for signal in hidden_in_literal)
    rate = suspicious_result_signals(
        "SELECT 150 AS denial_rate FROM sem.claims WHERE is_latest_submission",
        ["denial_rate"], [(150,)],
    )
    assert any("0..100" in signal for signal in rate)
    fanout = suspicious_result_signals(
        "SELECT claim_key, paid_amount FROM sem.remit_claims WHERE is_current_adjudication",
        ["claim_key", "paid_amount"], [("c1", 1.0), ("c1", 2.0)],
    )
    assert any("repeats" in signal for signal in fanout)
    upside_down = suspicious_result_signals(
        "SELECT denied_claims, remitted_claims FROM sem.remit_claims WHERE is_current_adjudication",
        ["denied_claims", "remitted_claims"], [(3, 2)],
    )
    assert any("greater than" in signal for signal in upside_down)
    all_null = suspicious_result_signals(
        "SELECT payer_name FROM sem.payers", ["payer_name"], [(None,), (None,)],
    )
    assert any("NULL" in signal for signal in all_null)


def test_empty_status_still_reports_a_missing_filter():
    status, signals = classify_execution(
        "SELECT claim_key FROM sem.claims WHERE payer_responsibility = 'ZZZ'",
        ExecutionResult(ok=True, columns=["claim_key"], rows=[], row_count=0),
    )
    assert status == "empty"
    assert any("is_latest_submission" in signal for signal in signals)


def test_happy_path_explores_samples_and_answers_without_repair():
    answer, executor, backend = run_script(make_connection(), [
        step("describe_schema"),
        step("sample_rows", relation="sem.claims", columns=["claim_key", "is_latest_submission"], limit=5),
        step("propose_sql", sql=LATEST_COUNT, rationale="one row per claim_key, latest submission only"),
        step("answer", sql=LATEST_COUNT, text="Two claims have a latest submission."),
    ])
    assert answer.refused is False
    assert answer.rows == [(2,)]
    assert answer.usage["repairs_used"] == 0
    assert answer.usage["sample_calls"] == 1
    assert [step["phase"] for step in answer.trace] == ["explore_schema", "sample_rows", "draft", "answer"]
    assert executor.sql_texts[0].startswith('SELECT "claim_key", "is_latest_submission" FROM "sem"."claims"')
    assert "is_latest_submission" in executor.sql_texts[1]
    assert backend.turns_completed == 4
    assert answer.usage["input_tokens"] > 0


def test_sql_error_is_repaired_once_and_the_model_sees_the_error():
    fixed = LATEST_COUNT
    answer, executor, backend = run_script(make_connection(), [
        step("describe_schema"),
        step("propose_sql", sql="SELECT no_such_column FROM sem.claims WHERE is_latest_submission"),
        step("propose_sql", sql=fixed),
        step("answer", sql=fixed, text="Two claims, counting each claim_key once on its latest submission."),
    ])
    assert answer.rows == [(2,)]
    assert answer.usage["repairs_used"] == 1
    assert any(step["status"] == "error" for step in answer.trace)
    assert "status: error" in json.dumps(backend.seen_messages[2])
    assert len(executor.sql_texts) == 2


def test_empty_result_is_repaired_and_a_dropped_filter_is_accepted():
    """The failure mode: a true empty population is 'fixed' by deleting the filter that caused it."""
    narrowed = (
        "SELECT claim_key FROM sem.claims "
        "WHERE is_latest_submission AND payer_responsibility = 'ZZZ'"
    )
    broadened = "SELECT claim_key, payer_responsibility FROM sem.claims WHERE is_latest_submission"
    answer, _, backend = run_script(make_connection(), [
        step("describe_schema"),
        step("propose_sql", sql=narrowed),
        step("propose_sql", sql=broadened),
        step("answer", sql=broadened, text="Latest submissions, the ZZZ filter matched nothing so it was removed."),
    ])
    assert answer.usage["repairs_used"] == 1
    assert "ZZZ" not in answer.sql
    assert {row[0] for row in answer.rows} == {"c1", "c2"}
    assert "status: empty" in json.dumps(backend.seen_messages[2])


def test_missing_latest_submission_filter_triggers_one_repair():
    unfiltered = "SELECT sum(charge_amount) AS billed FROM sem.claims"
    filtered = "SELECT sum(charge_amount) AS billed FROM sem.claims WHERE is_latest_submission"
    answer, _, backend = run_script(make_connection(), [
        step("describe_schema"),
        step("propose_sql", sql=unfiltered),
        step("propose_sql", sql=filtered),
        step("answer", sql=filtered, text="Billed amount on latest submissions only."),
    ])
    assert answer.rows == [(150.0,)]
    assert answer.usage["repairs_used"] == 1
    rendered = json.dumps(backend.seen_messages[2])
    assert "status: suspicious" in rendered
    assert "is_latest_submission" in rendered


def test_repair_budget_stops_at_two_and_does_not_execute_a_third_revision():
    bad = "SELECT no_such_column FROM sem.claims WHERE is_latest_submission"
    answer, executor, _ = run_script(make_connection(), [
        step("describe_schema"),
        step("propose_sql", sql=bad),
        step("propose_sql", sql=bad),
        step("propose_sql", sql=bad),
        step("propose_sql", sql=bad),
        step("answer", sql=bad, text="The query never parsed; submitting the last attempt."),
    ])
    assert executor.sql_texts.count(bad) == 3
    assert answer.usage["repairs_used"] == MAX_REPAIRS
    assert answer.sql == bad
    assert answer.rows is None
    assert any(step["status"] == "rejected" and step["phase"] == "propose_sql" for step in answer.trace)


def test_guard_rejection_is_an_error_result_not_a_crash():
    answer, executor, backend = run_script(make_connection(), [
        step("describe_schema"),
        step("propose_sql", sql="SELECT * FROM read_parquet('s3://x/y.parquet')"),
        step("propose_sql", sql=LATEST_COUNT),
        step("answer", sql=LATEST_COUNT, text="Two latest claims."),
    ])
    assert answer.rows == [(2,)]
    assert "not allowed" in json.dumps(backend.seen_messages[2]).lower()
    assert len(executor.sql_texts) == 2


def test_refuse_before_describe_does_not_query():
    answer, executor, _ = run_script(
        make_connection(),
        [step("refuse", reason="Patient identifiers are not in these views and I will not return them.")],
        question="List patient social security numbers.",
    )
    assert answer.refused is True
    assert answer.sql is None
    assert executor.sql_texts == []


def test_propose_before_describe_does_not_execute():
    answer, executor, _ = run_script(make_connection(), [
        step("propose_sql", sql=LATEST_COUNT),
        step("describe_schema"),
        step("sample_rows", relation="sem.claims"),
        step("propose_sql", sql=LATEST_COUNT),
        step("answer", sql="  " + LATEST_COUNT + " ; ", text="Two claims on their latest submission."),
    ])
    assert answer.rows == [(2,)]
    assert len(executor.sql_texts) == 2
    assert "LIMIT 5" in executor.sql_texts[0]
    assert "count(DISTINCT claim_key)" not in executor.sql_texts[0]
    assert "count(DISTINCT claim_key)" in executor.sql_texts[1]
    assert answer.usage["repairs_used"] == 0


def test_answer_sql_must_match_the_last_proposal():
    other = "SELECT count(*) FROM sem.claims WHERE is_latest_submission"
    answer, _, backend = run_script(make_connection(), [
        step("describe_schema"),
        step("propose_sql", sql=LATEST_COUNT),
        step("answer", sql=other, text="wrong sql"),
        step("answer", sql=LATEST_COUNT, text="Two distinct claim keys on the latest submission."),
    ])
    assert answer.sql == LATEST_COUNT
    assert "last proposed" in json.dumps(backend.seen_messages[3])


def test_empty_sample_does_not_consume_a_repair():
    answer, executor, _ = run_script(make_connection(), [
        step("describe_schema"),
        step("sample_rows", relation="sem.payers", columns=["payer_name"]),
        step("propose_sql", sql=LATEST_COUNT),
        step("answer", sql=LATEST_COUNT, text="Two latest claims. The payer view is empty and was only a sample."),
    ])
    assert answer.usage["repairs_used"] == 0
    assert answer.rows == [(2,)]
    sample = next(step for step in answer.trace if step["phase"] == "sample_rows")
    assert sample["row_count"] == 0
    assert len(executor.sql_texts) == 2


def test_malicious_column_name_never_reaches_duckdb():
    answer, executor, _ = run_script(make_connection(), [
        step("describe_schema"),
        step("sample_rows", relation="sem.claims", columns=["claim_key) FROM sem.claims; SELECT * FROM sem.claims --"]),
        step("propose_sql", sql=LATEST_COUNT),
        step("answer", sql=LATEST_COUNT, text="Two latest claims."),
    ])
    assert all(";" not in sql for sql in executor.sql_texts)
    assert answer.rows == [(2,)]


def test_script_that_ends_early_raises():
    with pytest.raises(ScriptExhausted):
        run_script(make_connection(), [step("describe_schema")])


def test_tool_names_cover_the_protocol():
    assert TOOL_NAMES == ("describe_schema", "sample_rows", "propose_sql", "answer", "refuse")


def test_config_is_accepted_by_the_eval_subagents_session_creator(tmp_path):
    document = configs_document()
    on_disk = json.loads(CONFIG_PATH.read_text())
    assert on_disk == document
    assert set(document) == {CONFIG_NAME}
    unknown = set(document[CONFIG_NAME]) - set(DEFAULT_TECHNIQUE_CONFIG)
    assert unknown == set()
    assert document[CONFIG_NAME]["max_tool_calls"] >= 3 + 1 + MAX_REPAIRS
    assert document[CONFIG_NAME]["glossary"] == "basic"
    assert document[CONFIG_NAME]["context_style"] == "goal"
    assert document[CONFIG_NAME]["enable_sql"] is True
    assert document[CONFIG_NAME]["enable_ask"] is False
    run_label = "a02-scripted-config"
    session = create_session(
        tmp_path,
        {"id": "q_scripted", "question": "How many distinct claims have we submitted?"},
        document[CONFIG_NAME],
        "scripted",
        run_label,
    )
    try:
        meta = json.loads((session / "session.json").read_text())
        assert meta["technique_config"]["result_checks"] is True
        assert meta["technique_config"]["sql_error_hints"] is True
    finally:
        shutil.rmtree(SESSIONS_ROOT / run_label, ignore_errors=True)


def test_drive_session_records_final_json_through_the_cli_adapter(tmp_path):
    run_label = "a02-scripted-drive"
    session = create_session(
        tmp_path,
        {"id": "q_drive", "question": "How many distinct claims are on their latest submission?"},
        configs_document()[CONFIG_NAME],
        "scripted",
        run_label,
    )
    calls = []

    def runner(command):
        assert command[1:4] == ["-m", "bench.agent_cli", "--session"]
        assert command[4] == str(session)
        args = command[5:]
        calls.append(args)
        if args[0] == "context":
            return 0, (
                "sem.claims(claim_key VARCHAR, is_latest_submission BOOLEAN, charge_amount DOUBLE)\n\n"
                "Business definitions\nignored"
            )
        if args[0] == "sql":
            if "count" in args[1].lower():
                return 0, json.dumps({"columns": ["claims"], "row_count": 1, "rows": [[2]], "truncated": False})
            return 0, json.dumps({
                "columns": ["claim_key", "is_latest_submission"],
                "row_count": 2,
                "rows": [["c1", True], ["c2", True]],
                "truncated": False,
            })
        if args[0] == "answer":
            sql = args[args.index("--sql") + 1]
            text = args[args.index("--text") + 1]
            (session / "final.json").write_text(json.dumps({
                "kind": "answer", "sql": sql, "text": text, "via": "sql",
            }))
            return 0, '{"recorded": true}'
        return 1, "ERROR: unexpected command"

    manifest = {
        "entries": [
            {"session": str(session), "config": CONFIG_NAME, "question_id": "q_drive"},
            {"session": str(session), "config": "someone_elses_arm", "question_id": "q_other"},
        ]
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    try:
        answers = drive_manifest(
            manifest_path,
            lambda entry, meta: ScriptedBackend([
                step("describe_schema"),
                step("sample_rows", relation="sem.claims", columns=["claim_key", "is_latest_submission"]),
                step("propose_sql", sql=LATEST_COUNT),
                step("answer", sql=LATEST_COUNT, text="Two claims on their latest submission."),
            ]),
            runner=runner,
        )
        assert len(answers) == 1
        final = json.loads((session / "final.json").read_text())
        assert final["kind"] == "answer"
        assert final["sql"] == LATEST_COUNT
        trace = json.loads((session / "a02_trace.json").read_text())
        assert trace["usage"]["repairs_used"] == 0
        assert [call[0] for call in calls] == ["context", "sql", "sql", "answer"]
        assert (SESSIONS_ROOT / run_label).exists()
    finally:
        shutil.rmtree(SESSIONS_ROOT / run_label, ignore_errors=True)


def test_drive_session_refuse_writes_a_refusal(tmp_path):
    run_label = "a02-scripted-refuse"
    session = create_session(
        tmp_path, {"id": "q_phi", "question": "Show member ids."},
        configs_document()[CONFIG_NAME], "scripted", run_label,
    )

    def runner(command):
        args = command[5:]
        if args[0] == "refuse":
            (session / "final.json").write_text(json.dumps({"kind": "refusal", "text": args[1]}))
            return 0, '{"recorded": true}'
        return 1, "ERROR: should have refused first"

    try:
        answer = drive_session(session, ScriptedBackend([
            step("refuse", reason="Member ids are not available in this session."),
        ]), runner=runner)
        assert answer.refused is True
        assert json.loads((session / "final.json").read_text())["kind"] == "refusal"
    finally:
        shutil.rmtree(SESSIONS_ROOT / run_label, ignore_errors=True)


def test_agent_refuses_to_start_without_a_backend():
    agent = A02ToolLoopAgent()
    with pytest.raises(RuntimeError, match="paid model API"):
        agent.answer("How many claims?", Tools(make_connection()))


def test_agent_answer_interface_uses_the_scripted_backend():
    agent = A02ToolLoopAgent(ScriptedBackend([
        step("describe_schema"),
        step("propose_sql", sql=LATEST_COUNT),
        step("answer", sql=LATEST_COUNT, text="Two latest claims."),
    ]))
    answer = agent.answer("How many distinct claims are on their latest submission?", Tools(make_connection()))
    assert answer.rows == [(2,)]
    assert answer.usage["sample_calls"] == 0


def test_planning_cost_grows_only_when_a_repair_happens():
    costs = documented_example_costs()
    happy = costs["happy_path"]
    repaired = costs["two_repairs"]
    assert happy["model_turns"] == 4
    assert happy["investigative_sql_calls"] == 2
    assert happy["latency_seconds"] == {"haiku": 6.0, "sonnet": 12.0}
    assert repaired["model_turns"] == 6
    assert repaired["repair_turns"] == 2
    assert repaired["investigative_sql_calls"] == 4
    assert repaired["latency_seconds"] == {"haiku": 9.0, "sonnet": 18.0}
    assert repaired["input_tokens_no_cache"] > happy["input_tokens_no_cache"]
    assert happy["input_tokens_cached_prefix"] < happy["input_tokens_no_cache"]
    assert costs["system_and_glossary_tokens"] > 200


def test_cli_write_config_and_drive_requires_a_script(tmp_path):
    destination = tmp_path / "a02.json"
    assert main(["write-config", str(destination)]) == 0
    assert json.loads(destination.read_text()) == configs_document()
    with pytest.raises(SystemExit):
        main(["drive", str(tmp_path / "manifest.json")])


def test_backend_module_has_no_model_client():
    package = Path(__file__).resolve().parent
    combined = "\n".join(
        path.read_text() for path in package.glob("*.py") if path.name != "test_scripted_backend.py"
    )
    assert "import anthropic" not in combined
    assert "Anthropic(" not in combined
    assert "messages.create" not in combined
