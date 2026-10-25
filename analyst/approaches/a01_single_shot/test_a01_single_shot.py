"""Scripted-backend tests for A01. No model API is called."""

from __future__ import annotations

import json
from pathlib import Path

import duckdb
import pytest
import sqlglot
import yaml
from sqlglot import exp

from analyst.approaches.a01_single_shot.adapter import (
    APPROACH_ID,
    as_eval_configs,
    drive_session,
    prepare_run,
    technique_config,
)
from analyst.approaches.a01_single_shot.backend import (
    ModelResponse,
    ScriptedBackend,
    ToolCall,
    refuse_response,
    submit_sql_response,
)
from analyst.approaches.a01_single_shot.cost import per_question_estimate
from analyst.approaches.a01_single_shot.dictionary import SEMANTIC_MODEL_PATH, build_data_dictionary
from analyst.approaches.a01_single_shot.executor import ConnectionQueryExecutor, QueryResult
from analyst.approaches.a01_single_shot.prompts import BASIC_GLOSSARY, render_system_prompt, render_user_prompt
from analyst.approaches.a01_single_shot.protocol import run_single_shot
from analyst.approaches.a01_single_shot.tools import TOOL_NAMES

ROOT = Path(__file__).resolve().parents[3]
VIEW_SQL = ROOT / "claude" / "schema" / "02_semantic_layer_views.sql"
TINY_MODEL = {
    "view_preference": ["sem.claims"],
    "views": {
        "sem.claims": {
            "grain": "one submission in the tiny model",
            "default_filter": "is_latest_submission",
            "use_for": "volume",
        }
    },
    "dimensions": {"claim_key": {"description": "Patient control number"}},
}


class FakeExecutor:
    def __init__(self, rows=None, error=None, dictionary="grain: one claim\ncolumns:\n- claim_key VARCHAR — patient control number"):
        self.rows = [("kept",)] if rows is None else rows
        self.error = error
        self.dictionary_text = dictionary
        self.executed_sql: list[str] = []

    def dictionary(self) -> str:
        return self.dictionary_text

    def execute(self, sql: str) -> QueryResult:
        self.executed_sql.append(sql)
        if self.error:
            raise self.error
        return QueryResult(columns=["v"], rows=self.rows)


def _tiny_executor(tmp_path, rows=("keep",)) -> ConnectionQueryExecutor:
    model_path = tmp_path / "model.yaml"
    model_path.write_text(yaml.safe_dump(TINY_MODEL))
    con = duckdb.connect()
    con.execute("CREATE SCHEMA sem")
    con.execute("CREATE TABLE sem.claims(claim_key VARCHAR)")
    con.executemany("INSERT INTO sem.claims VALUES (?)", [(row,) for row in rows])
    return ConnectionQueryExecutor(con, model_path=model_path)


def test_submit_sql_executes_once_and_the_answer_text_is_the_rows():
    executor = FakeExecutor(rows=[("kept",)])
    backend = ScriptedBackend([
        ModelResponse(
            tool_calls=[ToolCall(name="submit_sql", arguments={"sql": "SELECT claim_key FROM sem.claims"})],
            text="The answer is 99999",
        ),
        submit_sql_response("SELECT 1"),  # a repair the protocol must not consume
    ])
    outcome = run_single_shot("How many claims?", backend, executor)
    assert outcome.kind == "answer"
    assert outcome.model_calls == 1 and outcome.executions == 1
    assert outcome.sql == "SELECT claim_key FROM sem.claims"
    assert executor.executed_sql == [outcome.sql]
    assert len(backend.requests) == 1 and len(backend._responses) == 1
    assert "99999" not in outcome.text
    assert "kept" in outcome.text
    request = backend.requests[0]
    assert request.effort == "low"
    assert [tool["name"] for tool in request.tools] == list(TOOL_NAMES)
    assert "run_sql" not in json.dumps(request.tools)
    assert "How many claims?" in request.user
    assert "How many claims?" not in request.system
    assert "grain: one claim" in request.system
    assert "Invariants and common traps" not in request.system
    assert "Work in these steps" not in request.system
    assert BASIC_GLOSSARY.strip() in request.system


def test_execution_error_is_terminal():
    executor = FakeExecutor(error=RuntimeError("Referenced column missing"))
    backend = ScriptedBackend([
        submit_sql_response("SELECT missing FROM sem.claims"),
        submit_sql_response("SELECT claim_key FROM sem.claims"),
    ])
    outcome = run_single_shot("How many claims?", backend, executor)
    assert outcome.kind == "execution_error"
    assert outcome.model_calls == 1 and outcome.executions == 1
    assert outcome.sql == "SELECT missing FROM sem.claims"
    assert executor.executed_sql == [outcome.sql]
    assert len(backend.requests) == 1


def test_refuse_executes_nothing():
    executor = FakeExecutor()
    outcome = run_single_shot("List patient names", ScriptedBackend([refuse_response("PHI")]), executor)
    assert outcome.kind == "refusal" and outcome.text == "PHI"
    assert outcome.model_calls == 1 and outcome.executions == 0
    assert executor.executed_sql == []


def test_two_tool_calls_execute_nothing():
    executor = FakeExecutor()
    response = ModelResponse(tool_calls=[
        ToolCall(name="submit_sql", arguments={"sql": "SELECT 1 FROM sem.claims"}),
        ToolCall(name="submit_sql", arguments={"sql": "SELECT 2 FROM sem.claims"}),
    ])
    outcome = run_single_shot("How many claims?", ScriptedBackend([response]), executor)
    assert outcome.kind == "protocol_error" and outcome.executions == 0
    assert executor.executed_sql == []


def test_unknown_tool_and_prose_execute_nothing():
    executor = FakeExecutor()
    explored = run_single_shot(
        "How many claims?",
        ScriptedBackend([ModelResponse(tool_calls=[ToolCall(name="run_sql", arguments={"sql": "SELECT 1"})])]),
        executor,
    )
    prose = run_single_shot(
        "How many claims?",
        ScriptedBackend([ModelResponse(text="SELECT 1 FROM sem.claims")]),
        executor,
    )
    assert explored.kind == "protocol_error" and prose.kind == "protocol_error"
    assert explored.executions == 0 and prose.executions == 0
    assert executor.executed_sql == []


def test_json_string_arguments_are_accepted_once():
    executor = FakeExecutor(rows=[(1,)])
    response = ModelResponse(tool_calls=[ToolCall(
        name="submit_sql", arguments=json.dumps({"sql": "SELECT count(*) FROM sem.claims"}),
    )])
    outcome = run_single_shot("How many claims?", ScriptedBackend([response]), executor)
    assert outcome.kind == "answer" and outcome.executions == 1
    assert executor.executed_sql == ["SELECT count(*) FROM sem.claims"]


def test_guard_rejects_a_write_and_a_second_statement_without_another_call(tmp_path):
    executor = _tiny_executor(tmp_path)
    backend = ScriptedBackend([
        submit_sql_response("DELETE FROM sem.claims"),
        submit_sql_response("SELECT count(*) FROM sem.claims"),
    ])
    outcome = run_single_shot("Delete the claims", backend, executor)
    assert outcome.kind == "execution_error" and outcome.executions == 1 and outcome.model_calls == 1
    assert executor.con.execute("SELECT claim_key FROM sem.claims").fetchall() == [("keep",)]
    second = run_single_shot(
        "How many claims?",
        ScriptedBackend([submit_sql_response("SELECT count(*) FROM sem.claims; DELETE FROM sem.claims")]),
        executor,
    )
    assert second.kind == "execution_error" and second.executions == 1
    assert executor.con.execute("SELECT claim_key FROM sem.claims").fetchall() == [("keep",)]


def test_dictionary_describes_columns_and_does_not_profile_values(tmp_path):
    executor = _tiny_executor(tmp_path, rows=("UNIQUE_PAYER_ZZ",))
    text = executor.dictionary()
    assert "UNIQUE_PAYER_ZZ" not in text
    assert "one submission in the tiny model" in text
    assert "is_latest_submission" in text
    assert "claim_key" in text
    assert "Patient control number" in text
    assert "not applied to a SELECT" in text
    system = render_system_prompt(text)
    assert "Invariants and common traps" not in system
    assert "Which CPT codes have the highest denial rate" not in system


def _output_columns_by_view() -> dict[str, list[str]]:
    found = {}
    for tree in sqlglot.parse(VIEW_SQL.read_text(), read="duckdb"):
        if not isinstance(tree, exp.Create) or tree.this is None or tree.this.db != "sem":
            continue
        if not isinstance(tree.expression, exp.Select):
            continue
        found[tree.this.name] = [expression.alias_or_name for expression in tree.expression.expressions]
    return found


def test_full_catalog_prefix_is_the_glossary_and_dictionary_only():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA sem")
    for name, columns in _output_columns_by_view().items():
        select_list = ", ".join(f'NULL::VARCHAR AS "{column}"' for column in columns)
        con.execute(f'CREATE VIEW sem."{name}" AS SELECT {select_list}')
    dictionary = build_data_dictionary(con)
    question = "How many distinct claims (patient control numbers) have we submitted?"
    system = render_system_prompt(dictionary)
    user = render_user_prompt(question)
    estimate = per_question_estimate(system, user)
    assert estimate["model_calls"] == 1
    assert estimate["repair_rounds"] == 0
    assert estimate["exploratory_queries"] == 0
    assert estimate["sql_executions_when_the_model_submits"] == 1
    assert estimate["sql_executions_when_the_model_refuses_or_breaks_the_protocol"] == 0
    # The view SQL file is about 34k characters. Pasting it would blow this bound.
    assert len(dictionary) < 25000
    assert "any_value is not" not in dictionary
    assert "Which CPT codes have the highest denial rate" not in dictionary
    assert "is_current_adjudication" in dictionary
    assert "one 837P claim submission" in dictionary
    assert "Invariants and common traps" not in system
    assert question not in system and question in user
    assert estimate["prefix_tokens"] == (len(system) + 3) // 4
    # Measured on the view-SQL column lists typed as VARCHAR. Live DuckDB type names move this
    # by the difference in those names, a few hundred tokens, not a different prefix.
    assert estimate["prefix_tokens"] == 4072
    assert estimate["question_tokens"] == 20


def test_technique_config_is_legal_and_drive_rejects_anything_else(tmp_path, monkeypatch):
    import bench.agent_session as agent_session
    from bench.agent_session import create_session

    monkeypatch.setattr(agent_session, "SESSIONS_ROOT", tmp_path)
    from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG

    config = technique_config()
    assert set(config) == set(DEFAULT_TECHNIQUE_CONFIG)
    question = {"id": "q_probe", "question": "How many claims?"}
    session = create_session(tmp_path / "snap", question, config, "scripted", "a01")
    backend = ScriptedBackend([submit_sql_response("SELECT 1")])

    class ExplodingBackend:
        def complete(self, request):
            raise AssertionError("model was called for a non-a01 session")

    other = create_session(tmp_path / "snap", question, {"glossary": "pitfalls", "enable_metrics": True}, "scripted", "a01")
    with pytest.raises(ValueError, match="not the a01"):
        drive_session(other, ExplodingBackend(), executor=FakeExecutor())
    # The legal session is not driven here: it would need a snapshot. Creating it is the acceptance check.
    assert json.loads((session / "session.json").read_text())["technique_config"]["max_tool_calls"] == 1
    assert as_eval_configs() == {APPROACH_ID: config}
    assert backend.requests == []


def _mini_snapshot(tmp_path: Path) -> Path:
    data = tmp_path / "data" / "sem"
    data.mkdir(parents=True)
    con = duckdb.connect()
    preference = yaml.safe_load(SEMANTIC_MODEL_PATH.read_text())["view_preference"]
    con.execute("CREATE TABLE claims(claim_key VARCHAR)")
    con.execute("INSERT INTO claims VALUES ('c1'), ('c1'), ('c2')")
    con.execute(f"COPY claims TO '{data / 'sem.claims.parquet'}' (FORMAT PARQUET)")
    for view in preference:
        if view == "sem.claims":
            continue
        con.execute("CREATE TABLE stub(stub INTEGER)")
        con.execute("INSERT INTO stub VALUES (1)")
        con.execute(f"COPY stub TO '{data / (view + '.parquet')}' (FORMAT PARQUET)")
        con.execute("DROP TABLE stub")
    (tmp_path / "meta.json").write_text(json.dumps({
        "relations": list(preference), "dataset_id": "a01mini", "customer": "acme",
    }))
    return tmp_path


def test_prepare_drive_and_score_use_the_single_execution(tmp_path, monkeypatch):
    import bench.agent_session as agent_session
    import bench.eval_subagents as eval_subagents
    from bench.dataset import open_snapshot

    monkeypatch.setattr(agent_session, "SESSIONS_ROOT", tmp_path)
    monkeypatch.setattr(eval_subagents, "SESSIONS_ROOT", tmp_path)
    snap = _mini_snapshot(tmp_path / "snapshot")
    manifest_path = prepare_run("a01run", ["scripted"], repeats=1, only="q01_claims_submitted", snapshot_path=snap)
    manifest = json.loads(manifest_path.read_text())
    assert manifest["approach"] == APPROACH_ID
    assert len(manifest["entries"]) == 1
    entry = manifest["entries"][0]
    assert "Do not run bench/agent_cli.py" in entry["prompt"]
    assert "context" not in entry["prompt"]
    session = Path(entry["session"])
    gold_sql = "SELECT count(DISTINCT claim_key) AS claims FROM sem.claims"
    backend = ScriptedBackend([
        submit_sql_response(gold_sql),
        submit_sql_response("SELECT 0"),
    ])
    outcome = drive_session(session, backend)
    assert outcome.kind == "answer" and outcome.model_calls == 1 and outcome.executions == 1
    assert len(backend.requests) == 1
    # The dictionary was built from the sandboxed session and did not copy a stored claim key into the prompt.
    assert "c1" not in backend.requests[0].system
    assert "is_current_adjudication" in backend.requests[0].system
    with pytest.raises(ValueError, match="does not repair"):
        drive_session(session, ScriptedBackend([submit_sql_response("SELECT 0")]))
    case = next(case for case in eval_subagents.load_cases() if case["id"] == "q01_claims_submitted")
    gold_rows = open_snapshot(snap).execute(case["gold_sql"]).fetchall()
    assert gold_rows == [(2,)]
    scored = eval_subagents.score_session(session, case, gold_rows)
    assert scored["correct"] and scored["tool_calls"] == 1 and scored["final_kind"] == "answer"
    trace = json.loads((session / "a01_trace.json").read_text())
    assert trace["model_calls"] == 1 and trace["executions"] == 1 and trace["approach"] == APPROACH_ID


def test_scorer_marks_a_failed_execution_and_a_refusal(tmp_path, monkeypatch):
    import bench.agent_session as agent_session
    from bench.agent_session import create_session, score_session

    monkeypatch.setattr(agent_session, "SESSIONS_ROOT", tmp_path)
    snap = _mini_snapshot(tmp_path / "snapshot")
    question = {"id": "q_fail", "question": "How many claims?", "escalate": False}
    failed = create_session(snap, question, technique_config(), "scripted", "a01score")
    outcome = drive_session(failed, ScriptedBackend([submit_sql_response("SELECT no_such_column FROM sem.claims")]))
    assert outcome.kind == "execution_error" and outcome.executions == 1
    scored = score_session(failed, {**question, "gold_sql": "SELECT 1", "compare": {}}, [(1,)])
    assert scored["correct"] is False and "final SQL fails" in scored["why"]

    refusal_question = {"id": "q_phi", "question": "Show patient names", "expect_refusal": True}
    refused = create_session(snap, refusal_question, technique_config(), "scripted", "a01score")
    drive_session(refused, ScriptedBackend([refuse_response("PHI is not in these views")]))
    assert score_session(refused, refusal_question, None)["correct"] is True

    broken = create_session(snap, question, technique_config(), "scripted", "a01score")
    drive_session(broken, ScriptedBackend([ModelResponse(text="I think it is 2")]))
    assert score_session(broken, {**question, "compare": {}}, [(2,)])["final_kind"] is None
