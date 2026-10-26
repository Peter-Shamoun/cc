"""claude_code_cli layer: a fake ``claude -p`` worker writes calls.jsonl / final.json the way analyzer.cli does, over a
tiny bench-shaped snapshot. Checks streaming, re-execution of the final SQL, refusal/clarify mapping, Stop, and
the app wiring. No model, no network."""
import json
import subprocess
import sys
import threading
import time

import duckdb
import pytest
from fastapi.testclient import TestClient

from api.app import Settings, build_claude_code_cli_driver, create_app
from api.backends import DemoAuth, SimAuth
from api.claude_code_cli_driver import AnalyzerRunFailed, ClaudeCodeCliDriver
from api.live import Cancelled, LiveTap
from api.tests.conftest import LABS, events, parts

FAKE_WORKER = r'''
import json, os, sys, time
session_dir, plan = sys.argv[1], json.loads(os.environ["FAKE_WORKER_PLAN"])
for step in plan["calls"]:
    time.sleep(step.get("sleep_before", 0))
    with open(os.path.join(session_dir, "calls.jsonl"), "a") as calls_file:
        calls_file.write(json.dumps(step["record"]) + "\n")
time.sleep(plan.get("sleep_after_calls", 0))
for name, body in plan.get("files", {}).items():
    with open(os.path.join(session_dir, name), "w") as out:
        out.write(json.dumps(body))
print(json.dumps({"type": "result", "is_error": False, "num_turns": 4, "duration_ms": 1200, "duration_api_ms": 900,
                  "terminal_reason": "completed",
                  "modelUsage": {"fake-small-model": {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 100,
                                                      "cacheCreationInputTokens": 5}}}))
'''

FINAL_SQL = "SELECT payer_name, claim_count FROM sem.claims ORDER BY payer_name"


@pytest.fixture
def tiny_snapshot(tmp_path):
    snapshot_dir = tmp_path / "snapshot"
    (snapshot_dir / "data" / "sem").mkdir(parents=True)
    connection = duckdb.connect()
    connection.execute("CREATE TABLE c AS SELECT * FROM (VALUES ('Aetna', 3), ('Blue Cross', 5)) t(payer_name, claim_count)")
    connection.execute(f"COPY c TO '{snapshot_dir / 'data' / 'sem' / 'sem.claims.parquet'}' (FORMAT parquet)")
    (snapshot_dir / "data" / "lossless").mkdir()
    connection.execute("CREATE TABLE f AS SELECT * FROM (VALUES ('f1', 'REJECTED'), ('f2', 'PARSED'), ('f3', 'REJECTED')) "
                       "t(file_id, parse_status)")
    connection.execute(f"COPY f TO '{snapshot_dir / 'data' / 'lossless' / 'lossless.edi_source_file.parquet'}' "
                       "(FORMAT parquet)")
    (snapshot_dir / "meta.json").write_text(json.dumps({"customer": "acme_labs",
                                                        "relations": ["sem.claims", "lossless.edi_source_file"]}))
    return snapshot_dir


@pytest.fixture
def driver_factory(tiny_snapshot, tmp_path, monkeypatch):
    monkeypatch.setattr("bench.agent_session.SESSIONS_ROOT", tmp_path / "sessions")
    worker_script = tmp_path / "fake_worker.py"
    worker_script.write_text(FAKE_WORKER)
    started_processes = []

    def make(plan):
        def start_fake_worker(command, working_directory, environment, stdout_path, stderr_path):
            with open(stdout_path, "w") as stdout_file:
                process = subprocess.Popen([sys.executable, str(worker_script), str(stdout_path.parent)],
                                           env={**environment, "FAKE_WORKER_PLAN": json.dumps(plan)},
                                           stdout=stdout_file, start_new_session=True)
            started_processes.append(process)
            return process
        return ClaudeCodeCliDriver(tiny_snapshot, calls_poll_seconds=0.02, start_worker=start_fake_worker)

    make.started_processes = started_processes
    return make


def answer_plan(sleep_after_calls=0.0):
    return {"calls": [
        {"record": {"command": "describe", "argument": {"relation": "sem.claims"}, "ok": True, "result": "ok"}},
        {"record": {"command": "sql", "argument": {"sql": "SELECT nope FROM sem.claims"}, "ok": False,
                    "result": "Binder Error: column nope not found"}, "sleep_before": 0.05},
        {"record": {"command": "sql", "argument": {"sql": FINAL_SQL}, "ok": True, "result": {"row_count": 2}}},
        {"record": {"command": "answer", "argument": {"sql": FINAL_SQL, "text": "Blue Cross has the most claims."},
                    "ok": True, "result": "ok"}}],
        "sleep_after_calls": sleep_after_calls,
        "files": {"final.json": {"kind": "answer", "sql": FINAL_SQL, "text": "Blue Cross has the most claims.",
                                 "via": "sql", "result_warnings": []}}}


def run_driver(driver, question="Claims by payer?", cancelled=None):
    emitted = []
    tap = LiveTap(None, lambda event: emitted.append((time.perf_counter(), event)), cancelled=cancelled)
    answer = driver.answer(question, tap, cancelled or threading.Event())
    tap.finish(answer)
    return answer, tap, emitted


def test_steps_stream_while_the_worker_runs_and_the_final_sql_is_re_executed(driver_factory):
    answer, tap, emitted = run_driver(driver_factory(answer_plan(sleep_after_calls=0.6)))
    finished_at = time.perf_counter()
    steps = [(at, event) for at, event in emitted if event["type"].startswith("step.")]
    assert steps[0][1]["type"] == "step.explore"
    first_step_at = steps[0][0]
    assert finished_at - first_step_at > 0.4, "steps arrive while the worker is still running"
    results = [event for _, event in emitted if event["type"] == "step.sql_result"]
    assert [result["ok"] for result in results] == [False, True, True]
    assert results[0]["error"]["code"] == "binder_error" and results[1]["rowCount"] == 2
    assert results[2]["rowCount"] == 2 and [c["name"] for c in results[2]["columns"]] == ["payer_name", "claim_count"]
    repair = next(event for _, event in emitted if event["type"] == "step.repair_attempt")
    assert repair["revisedSql"] == FINAL_SQL
    assert answer.kind == "answer" and answer.rows == [["Aetna", 3], ["Blue Cross", 5]]
    assert tap.stats.queries == 3 and tap.stats.models["fake-small-model"] == {"inputTokens": 115,
                                                                               "outputTokens": 20, "calls": 4}


def test_refusal_and_clarification_finals_map(driver_factory):
    refusal = {"calls": [{"record": {"command": "refuse", "argument": {"reason": "That asks for patient names."},
                                     "ok": True, "result": "ok"}}],
               "files": {"final.json": {"kind": "refusal", "text": "That asks for patient names."}}}
    answer, tap, _ = run_driver(driver_factory(refusal))
    assert answer.kind == "refusal" and answer.refused and tap.refuse_args == {"reason": "That asks for patient names."}
    clarification = {"calls": [], "files": {"final.json": {"kind": "clarification", "text": "Which year?"}}}
    answer, _, _ = run_driver(driver_factory(clarification))
    assert answer.kind == "clarification" and answer.text == "Which year?"


LOSSLESS_SQL = "SELECT count(*) AS rejected FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'"
ESCALATION_REASON = "sem.* has no parse status for file 123-45-6789; need rejected files"


def escalation_plan(escalated=True):
    calls = [{"record": {"command": "escalate", "argument": {"reason": ESCALATION_REASON}, "ok": True,
                         "result": "ok"}}] if escalated else []
    calls.append({"record": {"command": "answer", "argument": {"sql": LOSSLESS_SQL, "text": "2 files were rejected."},
                             "ok": True, "result": "ok"}})
    files = {"final.json": {"kind": "answer", "sql": LOSSLESS_SQL, "text": "2 files were rejected.", "via": "sql"}}
    if escalated:
        files["escalation.json"] = {"escalated": True, "reason": ESCALATION_REASON, "customer": "acme_labs"}
    return {"calls": calls, "files": files}


def test_no_final_answer_fails_closed(driver_factory):
    with pytest.raises(AnalyzerRunFailed, match="without a final answer"):
        run_driver(driver_factory({"calls": []}))


def test_a_snapshot_escalation_is_shown_audited_and_re_executed_with_lossless(driver_factory):
    granted = []
    tap = LiveTap(None, (emitted := []).append)
    answer = driver_factory(escalation_plan()).answer("How many files were rejected?", tap, threading.Event(),
                                                      on_snapshot_escalation=granted.append)
    tap.finish(answer)
    assert answer.kind == "answer" and answer.rows == [[2]]
    (step,) = [event for event in emitted if event.get("probe") == "request_escalation"]
    assert step["type"] == "step.explore" and step["summary"].startswith("Reason: sem.* has no parse status")
    assert "123-45-6789" not in step["summary"]
    assert granted == [ESCALATION_REASON]          # the app masks it before it reaches the audit log
    # without a granted escalation the same lossless answer cannot be re-executed
    with pytest.raises(Exception, match="lossless"):
        run_driver(driver_factory(escalation_plan(escalated=False)))


def test_stop_kills_the_worker_promptly(driver_factory):
    plan = {"calls": [{"record": {"command": "list", "argument": {}, "ok": True, "result": "ok"}},
                      {"record": {"command": "sql", "argument": {"sql": FINAL_SQL}, "ok": True,
                                  "result": {"row_count": 2}}, "sleep_before": 30}]}
    driver = driver_factory(plan)
    cancelled = threading.Event()
    emitted = []

    def press_stop_on_first_step(event):
        emitted.append(event)
        if event["type"].startswith("step."):
            cancelled.set()

    tap = LiveTap(None, press_stop_on_first_step, cancelled=cancelled)
    started = time.monotonic()
    with pytest.raises(Cancelled):
        driver.answer("Claims?", tap, cancelled)
    assert time.monotonic() - started < 1
    driver_factory.started_processes[-1].wait(timeout=5)   # SIGTERM was sent; the worker is gone
    assert [event["type"] for event in emitted if event["type"].startswith("step.")] == ["step.explore"]


def test_worker_command_is_the_shipped_bundle_and_may_escalate(tiny_snapshot, tmp_path, monkeypatch):
    monkeypatch.setattr("bench.agent_session.SESSIONS_ROOT", tmp_path / "sessions")
    driver = ClaudeCodeCliDriver(tiny_snapshot)
    session_dir = driver.prepare_session("Claims by payer?")
    meta = json.loads((session_dir / "session.json").read_text())
    assert meta["technique_config"]["playbook"] and meta["technique_config"]["examples_k"] == 3
    assert meta["playbook_sha256"] and meta["question_id"] == "live_question"
    command = driver.worker_command(session_dir)
    assert command[:2] == ["claude", "-p"] and command[command.index("--model") + 1] == "haiku"
    assert command[command.index("--effort") + 1] == "low"
    disallowed = command[command.index("--disallowedTools") + 1:command.index("--output-format")]
    assert not [tool for tool in disallowed if "escalate" in tool]   # escalate is an ordinary, audited command


def test_app_streams_a_cli_answer_and_saves_it(driver_factory):
    app = create_app(settings=Settings(heartbeat_seconds=5), claude_code_cli_driver=driver_factory(answer_plan()))
    client = TestClient(app)
    assert client.get("/api/health").json()["analyzerInteraction"] == "claude_code_cli"
    assert client.get("/api/capabilities").json()["demo"] is False
    response = client.post("/api/chat", headers=LABS, json={"messages": [{"role": "user", "text": "Claims by payer?"}]})
    stream = events(response)
    assert stream[0]["type"] == "message.start" and stream[0]["model"] == "haiku"
    assert stream[-1]["type"] == "message.end" and stream[-1]["finishReason"] == "stop"
    table = next(part for part in parts(stream) if part["type"] == "table")
    assert table["rows"] == [["Aetna", 3], ["Blue Cross", 5]]
    conversation = client.get(f"/api/conversations/{stream[0]['conversationId']}", headers=LABS).json()
    assert conversation["messages"][-1]["finishReason"] == "stop"
    refused = client.post("/api/chat", headers=LABS, json={"messages": [{"role": "user", "text": "x"}],
                                                           "options": {"strategy": "candidates"}})
    assert refused.status_code == 422


def test_cli_layer_refuses_what_it_cannot_honour(tiny_snapshot):
    with pytest.raises(RuntimeError, match="expected one of"):
        build_claude_code_cli_driver(Settings(analyzer_interaction="mcp"), DemoAuth())
    with pytest.raises(RuntimeError, match="CC_API_ANALYZER_SNAPSHOT"):
        build_claude_code_cli_driver(Settings(analyzer_interaction="claude_code_cli", analyzer_snapshot_path=None),
                                     DemoAuth())
    cli_settings = Settings(analyzer_interaction="claude_code_cli", analyzer_snapshot_path=str(tiny_snapshot))
    assert not build_claude_code_cli_driver(cli_settings, DemoAuth()).queries_signed_in_customer
    assert build_claude_code_cli_driver(cli_settings, SimAuth(None, None)).queries_signed_in_customer  # run channel
    with pytest.raises(RuntimeError, match="supports demo auth"):
        build_claude_code_cli_driver(cli_settings, object())
    # a driver passed in directly is checked the same way: a snapshot driver never serves a real sign-in
    with pytest.raises(RuntimeError, match="supports demo auth"):
        create_app(auth=SimAuth(None, None), settings=Settings(), claude_code_cli_driver=ClaudeCodeCliDriver(tiny_snapshot))
    assert build_claude_code_cli_driver(Settings(analyzer_interaction="model_api"), DemoAuth()) is None
