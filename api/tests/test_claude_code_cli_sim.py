"""claude_code_cli on the real (simulated) login and data path: SimAuth → QueryPathOpener → ccsim.query.open_session.

The fake ``claude -p`` worker runs the real ``python3 -m analyzer.cli`` commands, which reach the run's reader session
over the run channel (analyzer/run_channel.py). Checks: each customer's run reads only that customer's lake; customer
A's run cannot read customer B's data (another session, a forged capability, B's S3 prefix, B's role); credentials
never reach the worker or the session folder; the folder is removed once the message is persisted. No model."""
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from analyzer.run_channel import CHANNEL_FILE, RunChannelError, send_command
from api.app import Settings, create_app
from api.backends import SimAuth
from api.claude_code_cli_driver import ClaudeCodeCliDriver, RunReaderSession
from api.tests.conftest import events, parts
from ccsim.aws.errors import AccessDenied
from ccsim.edi_mock import Scenario
from ccsim.infra import BUCKET
from ccsim.query import login, open_session
from ccsim.world import build_world

COUNT_SQL = "SELECT COUNT(DISTINCT claim_key) AS claims FROM sem.claims"
ROOT = Path(__file__).resolve().parents[2]

FAKE_WORKER = r'''
import json, os, subprocess, sys
session_dir, plan = sys.argv[1], json.loads(os.environ["FAKE_WORKER_PLAN"])
report = {"environment_keys": sorted(os.environ), "outputs": []}
for tail in plan["commands"]:
    argv = [sys.executable, "-m", "analyzer.cli", "--session", tail.pop(0) if tail and tail[0].startswith("/") else session_dir] + tail
    done = subprocess.run(argv, capture_output=True, text=True, cwd=os.environ["FAKE_WORKER_ROOT"])
    report["outputs"].append({"argv": tail, "code": done.returncode, "stdout": done.stdout})
if plan.get("forged_capability"):
    from analyzer.run_channel import send_command
    channel_path = os.path.join(session_dir, "run_channel.json")
    channel = json.load(open(channel_path))
    json.dump({**channel, "capability": "forged"}, open(channel_path, "w"))
    try:
        send_command(__import__("pathlib").Path(session_dir), ["--session", session_dir, "list"])
        report["forged"] = "served"
    except Exception as exc:
        report["forged"] = str(exc)
    json.dump(channel, open(channel_path, "w"))
open(os.environ["FAKE_WORKER_REPORT"], "w").write(json.dumps(report))
print(json.dumps({"type": "result", "is_error": False, "num_turns": 3, "duration_ms": 900, "duration_api_ms": 700,
                  "terminal_reason": "completed", "modelUsage": {}}))
'''


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    built = build_world(tmp_path_factory.mktemp("cli-world"),
                        Scenario(days=3, volume_scale=0.05, seed=4, burn_in_days=0, p_corrupt_file=1.0,
                                 p_duplicate_835_file=0.0))
    yield built
    built.aws.shutdown()


@pytest.fixture
def example_snapshot(tmp_path):
    """The bench snapshot is only the briefing's example library on this path; the fake worker never reads it."""
    snapshot_dir = tmp_path / "snapshot"
    snapshot_dir.mkdir()
    (snapshot_dir / "meta.json").write_text(json.dumps({"customer": "bench_customer", "relations": []}))
    return snapshot_dir


@pytest.fixture
def sim_driver(example_snapshot, tmp_path, monkeypatch):
    monkeypatch.setattr("bench.agent_session.SESSIONS_ROOT", tmp_path / "sessions")
    worker_script = tmp_path / "fake_worker.py"
    worker_script.write_text(FAKE_WORKER)
    reports = []

    def make(plan):
        def start_fake_worker(command, working_directory, environment, stdout_path, stderr_path):
            report_path = tmp_path / f"report-{len(reports)}.json"
            reports.append(report_path)
            with open(stdout_path, "w") as stdout_file:
                return subprocess.Popen([sys.executable, str(worker_script), str(stdout_path.parent)],
                                        env={**environment, "FAKE_WORKER_PLAN": json.dumps(plan),
                                             "FAKE_WORKER_REPORT": str(report_path), "FAKE_WORKER_ROOT": str(ROOT)},
                                        stdout=stdout_file, start_new_session=True)
        return ClaudeCodeCliDriver(example_snapshot, calls_poll_seconds=0.02, start_worker=start_fake_worker,
                                   queries_signed_in_customer=True)

    make.reports = reports
    make.sessions_root = tmp_path / "sessions" / "api_chat"
    return make


def answer_plan(sql=COUNT_SQL, extra=()):
    return {"commands": [["list"], ["sql", sql], *[list(item) for item in extra],
                         ["answer", "--sql", sql, "--text", "Distinct claims."]]}


def own_count(world, upn):
    return open_session(world.aws, world.dep, login(world.aws, upn)).con.execute(COUNT_SQL).fetchone()[0]


def chat(client, token, text="How many claims?"):
    response = client.post("/api/chat", headers={"Authorization": "Bearer " + token},
                           json={"messages": [{"role": "user", "text": text}]})
    assert response.status_code == 200, response.text
    return events(response)


def last_report(sim_driver) -> dict:
    return json.loads(sim_driver.reports[-1].read_text())


def test_each_customer_reads_only_its_own_lake_through_the_run_channel(world, sim_driver, monkeypatch):
    import ccsim.query as query
    opened = []
    real_open_session = query.open_session

    def spy(aws, dep, token, escalate=False, **kw):
        session = real_open_session(aws, dep, token, escalate=escalate, **kw)
        opened.append((session.customer, escalate, session.credentials))
        return session

    monkeypatch.setattr(query, "open_session", spy)
    plan = answer_plan(extra=[["sql", "SELECT secret_string FROM duckdb_secrets()"],
                              ["sql", f"SELECT count(*) FROM read_parquet('s3://{BUCKET}/acme_diagnostics/semantic_layer/*/*/*.parquet')"]])
    client = TestClient(create_app(auth=SimAuth(world.aws, world.dep), settings=Settings(heartbeat_seconds=5),
                                   claude_code_cli_driver=sim_driver(plan)))
    counts = {upn: own_count(world, upn) for upn in ("ana@xifin.example", "dev@xifin.example")}
    assert counts["ana@xifin.example"] != counts["dev@xifin.example"]
    opened.clear()
    for upn, customer in (("ana@xifin.example", "acme_labs"), ("dev@xifin.example", "acme_diagnostics")):
        stream = chat(client, login(world.aws, upn))
        assert stream[-1]["finishReason"] == "stop", stream
        table = next(part for part in parts(stream) if part["type"] == "table")
        assert table["rows"] == [[counts[upn]]]
        report = last_report(sim_driver)
        codes = [output["code"] for output in report["outputs"]]
        assert codes == [0, 0, 1, 1, 0], report      # secrets and another customer's files are refused
        assert all("ERROR" in output["stdout"] for output in report["outputs"][2:4])
        # one reader session per run, for this customer only, never escalated
        assert [(c, e) for c, e, _ in opened] == [(customer, False)]
        credentials = opened[-1][2]
        assert not any(key.startswith("AWS_") for key in report["environment_keys"])
        worker_saw = json.dumps(report)
        for secret in (credentials.access_key_id, credentials.secret_access_key, credentials.session_token):
            assert secret not in worker_saw
        opened.clear()
    assert sim_driver.sessions_root.is_dir() and not list(sim_driver.sessions_root.iterdir())   # folders (question text) removed once persisted


def test_customer_a_cannot_reach_customer_b_through_the_channel(world, sim_driver, tmp_path):
    """A's run: B's session folder, a forged capability and a dead channel are refused; B's data never shows up."""
    b_driver = sim_driver({"commands": []})
    b_session = b_driver.prepare_session("B's question", data_channel=True)
    plan = answer_plan()
    plan["commands"].insert(1, [str(b_session), "list"])          # a command naming another session
    plan["forged_capability"] = True
    driver = sim_driver(plan)
    auth = SimAuth(world.aws, world.dep)
    ctx = auth.authenticate(login(world.aws, "ana@xifin.example"), "r-a")
    from api.live import LiveTap
    tap = LiveTap(None, lambda event: None)
    kept = []
    answer = driver.answer("How many claims?", tap, threading.Event(), opener=ctx.opener, prepared_sessions=kept)
    assert answer.rows == [[own_count(world, "ana@xifin.example")]]
    report = last_report(sim_driver)
    other_session = report["outputs"][1]
    assert other_session["code"] == 1 and "ERROR" in other_session["stdout"]
    assert "not this run's channel" in report["forged"]
    assert not (b_session / "calls.jsonl").exists()                 # nothing ran against B's session
    # after the run the channel is gone: a late command reaches nothing
    assert not (kept[0] / CHANNEL_FILE).exists()
    with pytest.raises(RunChannelError):
        send_command(kept[0], ["--session", str(kept[0]), "list"])
    # and the CLI never falls back to the snapshot for a channel session
    late = subprocess.run([sys.executable, "-m", "analyzer.cli", "--session", str(kept[0]), "list"], cwd=ROOT,
                          capture_output=True, text=True)
    assert late.returncode == 1 and "run has ended" in late.stdout


def test_gates_5_and_6_hold_for_a_reader_session(world):
    """Under the channel: A's token cannot assume B's reader role (gate 5), A's credentials cannot read B's prefix
    even with the guard, the scoped secret and allowed_directories out of the way (gate 6)."""
    token_a = login(world.aws, "ana@xifin.example")
    with pytest.raises(AccessDenied):
        world.aws.sts.assume_role_with_web_identity(RoleArn=world.dep.reader_role("acme_diagnostics"),
                                                    RoleSessionName="cross", WebIdentityToken=token_a,
                                                    DurationSeconds=900)
    session_a = open_session(world.aws, world.dep, token_a, enforce_allowed_directories=False, scope_secret=False)
    with pytest.raises(Exception, match="403|Forbidden|AccessDenied|HTTP"):
        session_a.con.execute(f"SELECT count(*) FROM read_parquet('s3://{BUCKET}/acme_diagnostics/semantic_layer/"
                              "*/sem.claims/*.parquet')").fetchall()


def test_reader_session_is_reused_then_reopened_when_old():
    opened = []

    class CountingOpener:
        def open(self, escalate):
            opened.append(escalate)
            return object()

    now = [0.0]
    reader = RunReaderSession(CountingOpener(), reopen_seconds=600, clock=lambda: now[0])
    first = reader.open(False)
    assert reader.open(False) is first and opened == [False]
    now[0] = 601
    assert reader.open(False) is not first and opened == [False, False]


def test_sweep_removes_old_session_folders_only(sim_driver):
    driver = sim_driver({"commands": []})
    old = driver.prepare_session("old question")
    fresh = driver.prepare_session("fresh question")
    os.utime(old, (time.time() - 3600, time.time() - 3600))
    assert driver.sweep_expired_sessions(retention_seconds=600) == 1
    assert not old.exists() and fresh.exists()
    with pytest.raises(ValueError):
        driver.discard_session(Path("/tmp"))


def test_worker_environment_is_an_allowlist():
    from api.claude_code_cli_driver import worker_environment
    source = {"PATH": "/bin", "HOME": "/home/x", "HTTPS_PROXY": "http://proxy", "ANTHROPIC_API_KEY": "sk-paid",
              "ANTHROPIC_BASE_URL": "https://x", "CLAUDE_CODE_USE_BEDROCK": "1", "CLAUDE_CODE_USE_VERTEX": "1",
              "AWS_ACCESS_KEY_ID": "AKIA", "AWS_SECRET_ACCESS_KEY": "s", "CC_TRANSCRIPT_KEYS_DIR": "/keys",
              "CC_API_ALLOW_PAID_API": "1", "GH_TOKEN": "t"}
    environment = worker_environment(source)
    assert set(environment) == {"PATH", "HOME", "HTTPS_PROXY", "PYTHONPATH"}


def test_stop_kills_the_whole_group_even_after_the_worker_exited(tmp_path):
    """A worker that exits leaving a child behind: the child is killed too."""
    from api.claude_code_cli_driver import stop_worker_process
    pid_file = tmp_path / "child.pid"
    worker = subprocess.Popen([sys.executable, "-c", (
        "import subprocess, sys; child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
        f"open({str(pid_file)!r}, 'w').write(str(child.pid))")], start_new_session=True)
    worker.wait(timeout=10)
    child_pid = int(pid_file.read_text())
    stop_worker_process(worker)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        try:
            os.waitpid(child_pid, os.WNOHANG)
        except ChildProcessError:
            pass
        time.sleep(0.05)
    else:
        pytest.fail("the worker's child survived Stop")


def test_shutdown_kills_running_workers(sim_driver):
    driver = sim_driver({"commands": []})
    sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
    driver.live_workers.add(sleeper)
    driver.shutdown()
    assert sleeper.wait(timeout=5) != 0


def test_sim_principal_is_the_token_subject_not_the_email(world):
    ctx = SimAuth(world.aws, world.dep).authenticate(login(world.aws, "ana@xifin.example"), "r1")
    claims = world.aws.cognito.verify_id_token(login(world.aws, "ana@xifin.example"))
    assert ctx.principal.user == claims["sub"] != claims["email"]
