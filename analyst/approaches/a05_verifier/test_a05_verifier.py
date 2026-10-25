"""Scripted-backend tests for A05. No model API and no generator run.

    python -m pytest analyst/approaches/a05_verifier/test_a05_verifier.py -q
"""
from __future__ import annotations

import json
import shutil
import uuid
from pathlib import Path

import duckdb

from analyst.approaches.a05_verifier.adapter import (
    CONFIG_NAME,
    RUNNER,
    TECHNIQUE_CONFIG,
    configs,
    install_protocol,
    prepare,
    run_manifest,
)
from analyst.approaches.a05_verifier.backend import ScriptedBackend
from analyst.approaches.a05_verifier.checklist import CHECK_NAMES, checklist_sha256, render_checklist
from analyst.approaches.a05_verifier.executor import ConnectionExecutor, execute_on_query_session
from analyst.approaches.a05_verifier.prompts import (
    ANALYST_SYSTEM,
    PLAN_FIELDS,
    critic_user_message,
    result_digest,
    system_for_critic,
)
from analyst.approaches.a05_verifier.protocol import run_protocol
from bench.agent_cli import DEFAULT_TECHNIQUE_CONFIG
from bench.agent_session import SESSIONS_ROOT, score_session
from bench.eval_subagents import score

ROOT = Path(__file__).resolve().parents[3]
QUESTION = "How many distinct claims (patient control numbers) have we submitted?"
SECRET = "SECRET_RATIONALE_do_not_forward"
SCHEMA = "sem.claims(claim_key VARCHAR, charge_amount DOUBLE, is_latest_submission BOOLEAN)"

PLAN = {
    "grain": "one claim_key",
    "population": "every submission row in sem.claims",
    "time_basis": "none",
    "measure": "count of claim_key",
    "units": "claims",
}
WRONG_SQL = "SELECT count(*) AS submissions FROM sem.claims"
RIGHT_SQL = "SELECT count(DISTINCT claim_key) AS claims FROM sem.claims"


def _lab_connection():
    connection = duckdb.connect()
    connection.execute("CREATE SCHEMA sem")
    connection.execute("""
        CREATE TABLE sem.claims (
            claim_key VARCHAR, charge_amount DOUBLE,
            is_latest_submission BOOLEAN, payer_responsibility VARCHAR, submission_date DATE
        )
    """)
    connection.execute("""
        INSERT INTO sem.claims VALUES
        ('C1', 100, true, 'P', DATE '2026-01-01'),
        ('C1', 80, false, 'P', DATE '2025-12-01'),
        ('C2', 50, true, 'S', DATE '2026-01-03')
    """)
    connection.execute("SET enable_external_access = false")
    connection.execute("SET lock_configuration = true")
    return connection


def _executor():
    return ConnectionExecutor(_lab_connection(), SCHEMA)


def _plan_json(extra: dict | None = None) -> str:
    payload = {"plan": PLAN, "sql": WRONG_SQL, "rationale": SECRET}
    if extra:
        payload.update(extra)
    # The smuggled SQL and the scratchpad must not be executed or shown to the critic.
    return "thinking " + SECRET + "\n" + json.dumps(payload)


def _sql_json(sql: str, *, prose: str = "n claims") -> str:
    return "draft " + SECRET + "\n" + json.dumps({"sql": sql, "prose": prose})


def _pair_json(sql: str, *, prose: str = "n claims") -> str:
    return json.dumps({"plan": PLAN, "sql": sql, "prose": prose})


def _accept_json() -> str:
    checks = {name: {"ok": True, "note": "matches the question"} for name in CHECK_NAMES}
    return json.dumps({"verdict": "accept", "checks": checks, "objections": [], "sql": "SELECT 999"})


def _object_json() -> str:
    checks = {name: {"ok": True, "note": "ok"} for name in CHECK_NAMES}
    checks["population"] = {"ok": False, "note": "count(*) counts submissions, not distinct claims"}
    return json.dumps({
        "verdict": "accept",  # the failed check has to override this
        "checks": checks,
        "objections": ["count submissions rather than distinct claim_key"],
        "sql": "SELECT 999 FROM sem.claims",
    })


def _critic_users(backend: ScriptedBackend) -> list[str]:
    return [call["user"] for call in backend.calls if call["role"] == "critic"]


def test_checklist_covers_the_four_checks_and_hashes_stable():
    text = render_checklist()
    for name in CHECK_NAMES:
        assert f"## {name}:" in text
    assert "payer_responsibility" in text
    assert "is_current_adjudication" in text
    assert "100.0" in text
    assert "Invariants and common traps" not in text
    assert checklist_sha256() == checklist_sha256()
    assert len(checklist_sha256()) == 64


def test_accept_submits_the_sql_step_and_hides_the_analyst_scratchpad():
    backend = ScriptedBackend([
        ("analyst", _plan_json()),
        ("analyst", _sql_json(RIGHT_SQL, prose="two claims")),
        ("critic", _accept_json()),
    ])
    executor = _executor()
    trace = run_protocol(QUESTION, backend, executor)
    assert trace.outcome == "accepted"
    assert trace.revised is False
    assert trace.submitted_sql == RIGHT_SQL
    assert executor.submitted == ("answer", RIGHT_SQL, "two claims")
    assert [call["phase"] for call in backend.calls] == ["plan", "sql", "critic"]
    assert [attempt["sql"] for attempt in trace.attempts] == [RIGHT_SQL]
    assert WRONG_SQL not in backend.calls[1]["user"]
    critic = backend.calls[2]
    assert SECRET not in critic["system"] and SECRET not in critic["user"]
    assert ANALYST_SYSTEM.splitlines()[0] not in critic["system"]
    assert "Critic checklist (a05)" in critic["system"]
    assert "Critic checklist (a05)" not in backend.calls[0]["system"]
    visible_plan = {field: PLAN[field] for field in PLAN_FIELDS}
    digest = result_digest(["claims"], [[2]])
    assert critic["user"] == critic_user_message(QUESTION, visible_plan, RIGHT_SQL, digest)
    assert "SELECT 999" not in trace.submitted_sql


def test_failed_check_overrides_accept_and_revises_once():
    backend = ScriptedBackend([
        ("analyst", _plan_json()),
        ("analyst", _sql_json(WRONG_SQL, prose="SECRET_PROSE_initial")),
        ("critic", _object_json()),
        ("analyst", _pair_json(RIGHT_SQL, prose="two distinct claims")),
    ])
    executor = _executor()
    trace = run_protocol(QUESTION, backend, executor)
    assert trace.outcome == "revised" and trace.revised is True
    assert trace.submitted_sql == RIGHT_SQL
    assert executor.submitted[0] == "answer" and executor.submitted[1] == RIGHT_SQL
    assert [call["role"] for call in backend.calls] == ["analyst", "analyst", "critic", "analyst"]
    assert sum(call["role"] == "critic" for call in backend.calls) == 1
    critic_user = _critic_users(backend)[0]
    assert SECRET not in critic_user and "SECRET_PROSE_initial" not in critic_user
    assert "count submissions rather than distinct claim_key" in backend.calls[3]["user"]
    assert "SELECT 999" not in backend.calls[3]["user"]
    assert trace.critic["accept"] is False


def test_unparseable_critic_triggers_one_revision_and_stops():
    backend = ScriptedBackend([
        ("analyst", _plan_json()),
        ("analyst", _sql_json(WRONG_SQL)),
        ("critic", "not json at all"),
        ("analyst", _pair_json(RIGHT_SQL)),
    ])
    trace = run_protocol(QUESTION, backend, _executor())
    assert trace.outcome == "revised"
    assert trace.critic["accept"] is False
    assert "unparseable" in trace.critic["objections"][0]
    assert len(backend.responses) == 0  # the second critic was not requested


def test_refusal_skips_sql_and_the_critic():
    backend = ScriptedBackend([("analyst", json.dumps({"refuse": "That asks for patient names."}))])
    executor = _executor()
    trace = run_protocol("List patient names.", backend, executor)
    assert trace.outcome == "refused" and trace.refused is True
    assert executor.submitted == ("refusal", "That asks for patient names.")
    assert trace.attempts == []
    assert [call["role"] for call in backend.calls] == ["analyst"]


def test_execution_error_repairs_once_before_the_critic():
    backend = ScriptedBackend([
        ("analyst", _plan_json()),
        ("analyst", _sql_json("SELECT * FROM read_text('secrets.txt')")),
        ("analyst", _pair_json(RIGHT_SQL)),
        ("critic", _accept_json()),
    ])
    executor = _executor()
    trace = run_protocol(QUESTION, backend, executor)
    assert trace.outcome == "accepted"
    assert trace.execution_repairs == 1
    assert trace.submitted_sql == RIGHT_SQL
    assert trace.attempts[0]["ok"] is False and "not allowed" in trace.attempts[0]["error"]
    assert [call["phase"] for call in backend.calls] == ["plan", "sql", "execution_repair", "critic"]
    assert "read_text" not in backend.calls[3]["user"]  # the critic sees the SQL that returned rows


def test_two_execution_failures_do_not_call_the_critic():
    backend = ScriptedBackend([
        ("analyst", _plan_json()),
        ("analyst", _sql_json("SELECT * FROM read_text('secrets.txt')")),
        ("analyst", _pair_json("SELECT * FROM sem.no_such_view")),
    ])
    trace = run_protocol(QUESTION, backend, _executor())
    assert trace.outcome == "no_answer"
    assert trace.submitted_sql is None
    assert [call["role"] for call in backend.calls] == ["analyst", "analyst", "analyst"]


def test_revision_that_fails_to_execute_submits_nothing():
    backend = ScriptedBackend([
        ("analyst", _plan_json()),
        ("analyst", _sql_json(WRONG_SQL)),
        ("critic", _object_json()),
        ("analyst", _pair_json("SELECT * FROM sem.missing")),
    ])
    executor = _executor()
    trace = run_protocol(QUESTION, backend, executor)
    assert trace.outcome == "no_answer"
    assert executor.submitted is None
    assert trace.revised is False


def test_unparseable_analyst_does_not_execute():
    backend = ScriptedBackend([("analyst", "I think the answer is 2")])
    executor = _executor()
    trace = run_protocol(QUESTION, backend, executor)
    assert trace.outcome == "no_answer" and executor.submitted is None and trace.attempts == []


class _RecordingConnection:
    def __init__(self, connection):
        self._connection = connection
        self.statements: list[str] = []

    def execute(self, sql, *args):
        self.statements.append(sql)
        return self._connection.execute(sql, *args)


def test_query_session_path_guards_before_duckdb():
    inner = _lab_connection()
    recording = _RecordingConnection(inner)

    class _Session:
        con = recording

    blocked = execute_on_query_session(_Session(), "SELECT * FROM read_text('secrets.txt')")
    assert blocked.ok is False and recording.statements == [] and "not allowed" in blocked.error
    ran = execute_on_query_session(_Session(), RIGHT_SQL)
    assert ran.ok and ran.rows == [[2]]
    assert ran.sql == RIGHT_SQL
    assert recording.statements[0].startswith("SELECT * FROM (")
    assert "agent_query" in recording.statements[0] and "LIMIT 1000" in recording.statements[0]


def test_technique_config_is_legal_and_matches_the_json_file():
    assert set(TECHNIQUE_CONFIG) <= set(DEFAULT_TECHNIQUE_CONFIG)
    loaded = json.loads((ROOT / "analyst/approaches/a05_verifier/config.json").read_text())
    assert loaded == configs()
    assert TECHNIQUE_CONFIG["glossary"] == "basic"
    assert TECHNIQUE_CONFIG["result_checks"] is False
    assert TECHNIQUE_CONFIG["max_tool_calls"] == 3


def test_install_protocol_rewrites_only_a05_entries(tmp_path):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    manifest = {
        "entries": [
            {"session": "s1", "config": CONFIG_NAME, "prompt": "BASE PROMPT"},
            {"session": "s2", "config": "base", "prompt": "OTHER"},
        ]
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest))
    install_protocol(run_dir)
    install_protocol(run_dir)  # a second call must not wrap the briefing twice
    got = json.loads((run_dir / "manifest.json").read_text())
    assert got["entries"][1]["prompt"] == "OTHER"
    assert got["entries"][0]["prompt"].count("BASE PROMPT") == 1
    assert RUNNER in got["entries"][0]["prompt"]
    assert got["entries"][0]["runner"] == RUNNER
    assert got["checklist_sha256"] == checklist_sha256()


def _tiny_snapshot(root: Path) -> Path:
    snap = root / "snap"
    data = snap / "data" / "sem"
    data.mkdir(parents=True)
    connection = duckdb.connect()
    connection.execute("""
        CREATE TABLE claims AS SELECT * FROM (VALUES
            ('C1', 100.0, true, 'P', DATE '2026-01-01'),
            ('C1', 80.0, false, 'P', DATE '2025-12-01'),
            ('C2', 50.0, true, 'S', DATE '2026-01-03')
        ) AS t(claim_key, charge_amount, is_latest_submission, payer_responsibility, submission_date)
    """)
    connection.execute(f"COPY claims TO '{data / 'sem.claims.parquet'}' (FORMAT PARQUET)")
    (snap / "meta.json").write_text(json.dumps({"relations": ["sem.claims"]}))
    return snap


def test_prepare_run_and_score_through_eval_subagents(tmp_path):
    """The real manifest, the sandbox, and score(). The model is the scripted backend."""
    snap = _tiny_snapshot(tmp_path)
    run = "a05-scripted-" + uuid.uuid4().hex[:8]
    run_dir = SESSIONS_ROOT / run
    try:
        prepare(run, ["scripted"], repeats=1, only="q01_claims_submitted", snapshot_path=snap)
        manifest = json.loads((run_dir / "manifest.json").read_text())
        assert list(manifest["configs"]) == [CONFIG_NAME]
        assert manifest["entries"][0]["runner"] == RUNNER
        assert "A05_VERIFIER_RUNNER" in manifest["entries"][0]["prompt"]
        backend = ScriptedBackend([
            ("analyst", _plan_json()),
            ("analyst", _sql_json(WRONG_SQL)),
            ("critic", _object_json()),
            ("analyst", _pair_json(RIGHT_SQL, prose="two claims")),
        ])
        traces = run_manifest(run_dir, backend)
        assert traces[0]["outcome"] == "revised"
        assert SECRET not in _critic_users(backend)[0]
        session = Path(manifest["entries"][0]["session"])
        final = json.loads((session / "final.json").read_text())
        assert final["sql"] == RIGHT_SQL and final["kind"] == "answer"
        calls = [json.loads(line) for line in (session / "calls.jsonl").read_text().splitlines()]
        assert [call["command"] for call in calls if call["command"] == "sql"] == ["sql", "sql"]
        out = score(run)
        summary = out["summary"][0]
        assert summary["config"] == CONFIG_NAME
        assert summary["accuracy"] == 1.0 and summary["no_answer"] == 0
        assert summary["mean_tool_calls"] == 2
        case = {"id": "q01_claims_submitted", "compare": {}}
        assert score_session(session, case, [(2,)])["correct"]
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)
