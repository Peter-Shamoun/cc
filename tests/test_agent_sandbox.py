"""The evaluated-agent sandbox: CLI gating, guard, logging, single final answer, scoring by re-execution."""
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from bench.agent_session import create_session, score_session
from bench.dataset import open_snapshot, snapshot
from ccsim.edi_mock import Scenario

ROOT = Path(__file__).resolve().parent.parent
CASES = {c["id"]: c for c in yaml.safe_load((ROOT / "bench/questions.yaml").read_text())}


@pytest.fixture(scope="module")
def snap(tmp_path_factory):
    return snapshot(Scenario(days=15, volume_scale=.2, seed=3), cache=tmp_path_factory.mktemp("snap"))


def cli(session, *args):
    r = subprocess.run([sys.executable, str(ROOT / "bench/agent_cli.py"), "--session", str(session), *args],
                       capture_output=True, text=True, cwd="/")
    return r.returncode, r.stdout


def gold(snap, case):
    return open_snapshot(snap, escalate=case.get("escalate", False)).execute(case["gold_sql"]).fetchall()


def test_sql_answer_is_scored_by_re_execution(snap):
    case = CASES["q01_claims_submitted"]
    s = create_session(snap, case, {}, "test", "pytest")
    assert cli(s, "context")[0] == 0
    code, out = cli(s, "sql", "SELECT count(DISTINCT claim_key) AS n FROM sem.claims")
    assert code == 0 and json.loads(out)["row_count"] == 1
    assert cli(s, "answer", "--sql", "SELECT count(DISTINCT claim_key) FROM sem.claims", "--text", "n")[0] == 0
    assert cli(s, "answer", "--sql", "SELECT 1 FROM sem.claims", "--text", "again")[0] == 1    # one final only
    r = score_session(s, case, gold(snap, case))
    assert r["correct"] and r["tool_calls"] == 1 and r["tool_errors"] == 1


def test_disabled_commands_and_guard(snap):
    case = CASES["q01_claims_submitted"]
    s = create_session(snap, case, {}, "test", "pytest")
    assert "not available" in cli(s, "metrics", '{"metrics": ["remitted_claims"]}')[1]
    assert "not allowed" in cli(s, "sql", f"SELECT * FROM read_text('{snap}/truth.json')")[1]
    assert "ERROR" in cli(s, "sql", "SELECT * FROM lossless.edi_source_file")[1]
    calls = [json.loads(l) for l in (s / "calls.jsonl").read_text().splitlines()]
    assert len(calls) == 3 and not any(c["ok"] for c in calls)


def test_hardcoded_answer_is_not_grounded(snap):
    case = CASES["q01_claims_submitted"]
    s = create_session(snap, case, {}, "test", "pytest")
    n = gold(snap, case)[0][0]
    cli(s, "answer", "--sql", f"SELECT {n} AS n", "--text", "guessed")
    r = score_session(s, case, gold(snap, case))
    assert not r["correct"] and "not grounded" in r["why"]


def test_metrics_answer_and_tool_budget(snap):
    case = CASES["q08_denial_rate_by_payer"]
    s = create_session(snap, case, {"enable_metrics": True, "max_tool_calls": 2}, "test", "pytest")
    request = json.dumps({"metrics": ["denial_rate"], "group_by": ["payer_name"], "min_group_size": 1})
    assert cli(s, "metrics", request)[0] == 0
    assert cli(s, "sql", "SELECT 1 FROM sem.claims")[0] == 0
    assert "budget" in cli(s, "sql", "SELECT 1 FROM sem.claims")[1]
    assert cli(s, "answer", "--metrics", request, "--text", "rates")[0] == 0
    assert score_session(s, case, gold(snap, case))["correct"]


def test_refusal_scoring(snap):
    case = CASES["q30_phi_names"]
    s = create_session(snap, case, {}, "test", "pytest")
    cli(s, "refuse", "PHI")
    assert score_session(s, case, None)["correct"]
    s2 = create_session(snap, case, {}, "test", "pytest")
    cli(s2, "answer", "--sql", "SELECT claim_key FROM sem.claims", "--text", "here")
    assert not score_session(s2, case, None)["correct"]


def test_unknown_technique_setting_fails_fast(snap):
    with pytest.raises(ValueError):
        create_session(snap, CASES["q01_claims_submitted"], {"enable_magic": True}, "test", "pytest")
