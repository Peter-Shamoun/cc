"""Analyzer v1: tools, loop, scripted model, and the eval_subagents adapter.

The simulator PHI and tenant tests live in test_analyzer_sim.py. Everything here uses
an in-memory DuckDB or a tiny Parquet snapshot, and the scripted LLM (no network).
"""
import json
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from analyzer.answer import AnalyzerAnswer
from analyzer.config import HAIKU_MODEL, SONNET_MODEL, AnalyzerConfig
from analyzer.llm import AnthropicLLM, BedrockLLM, PaidAPIDisabled, ScriptedLLM, anthropic_request_kwargs, bedrock_request_body
from analyzer.loop import Analyzer
from analyzer.opener import SessionHandle, SnapshotOpener
from analyzer.tools import ToolError, ToolLayer, execute_with_time_limit
from bench.agent_session import score_session
from bench.compare import match


def _claims_db(rows):
    con = duckdb.connect()
    con.execute("CREATE SCHEMA sem")
    con.execute("CREATE TABLE sem.claims (claim_key VARCHAR, charge_amount DOUBLE, name_first VARCHAR)")
    con.executemany("INSERT INTO sem.claims VALUES (?, ?, ?)", rows)
    return con


class MemoryOpener:
    def __init__(self, plain, escalated=None, customer="acme_labs", user="ana@xifin.example"):
        self.plain = plain
        self.escalated = escalated if escalated is not None else plain
        self.customer = customer
        self.user = user
        self.opened = []

    def open(self, escalate: bool) -> SessionHandle:
        self.opened.append(escalate)
        version = "escalated" if escalate else "sem-only"
        connection = self.escalated if escalate else self.plain
        return SessionHandle(self.customer, self.user, version, connection)


def _quiet(**overrides) -> AnalyzerConfig:
    base = dict(glossary="none", data_dictionary=False, data_profile=False, sql_error_hints=True,
                result_checks=True, verify=True, self_correct="errors+result_signals", context_style="goal",
                refusal_prompt="explicit", candidates=1, cascade="off", model=HAIKU_MODEL)
    base.update(overrides)
    return AnalyzerConfig(**base)


def _final(sql, text="n"):
    return {"tool_calls": [{"name": "final_answer", "arguments": {"sql": sql, "answer": text}}]}


def test_screening_flags_map_and_reject_unknown():
    winner = json.loads(Path("research/screening_configs.json").read_text())["s0110001"]
    config = AnalyzerConfig.from_technique(winner)
    assert config.glossary == "pitfalls"
    assert config.data_dictionary and config.data_profile
    assert config.context_style == "procedural" and config.effort == "low"
    assert config.candidates == 1 and config.cascade == "off" and not config.enable_metrics
    assert AnalyzerConfig().verify and AnalyzerConfig().self_correct == "errors+result_signals"
    screened = AnalyzerConfig.from_technique({"glossary": "basic"})
    assert screened.glossary == "basic" and not screened.data_dictionary and not screened.verify
    with pytest.raises(ValueError, match="unknown"):
        AnalyzerConfig.from_technique({"enable_magic": True})
    with pytest.raises(ValueError):
        AnalyzerConfig(candidates=9)


def test_paid_clients_do_not_call_out():
    kwargs = anthropic_request_kwargs(model=HAIKU_MODEL, system="s", messages=[], tools=[], max_tokens=16,
                                      effort="high", prompt_cache=False)
    assert "temperature" not in kwargs and kwargs["output_config"] == {"effort": "high"}
    body = bedrock_request_body(system="s", messages=[], tools=[], max_tokens=16, effort="low", prompt_cache=True)
    assert body["output_config"] == {"effort": "low"} and isinstance(body["system"], list)
    with pytest.raises(PaidAPIDisabled):
        AnthropicLLM().complete(model=HAIKU_MODEL, system="s", messages=[], tools=[], max_tokens=8)
    with pytest.raises(PaidAPIDisabled):
        BedrockLLM().complete(model=SONNET_MODEL, system="s", messages=[], tools=[], max_tokens=8)


def test_run_sql_is_guarded_limited_and_redacts_phi():
    con = _claims_db([("LB1", 10.0, "Ada"), ("LB2", 5.0, "Bea"), ("LB3", 1.0, "Cy")])
    recorded = []

    class Recording:
        def execute(self, sql, *params):
            recorded.append(sql)
            return con.execute(sql, *params)

        def interrupt(self):
            con.interrupt()

    tools = ToolLayer(MemoryOpener(Recording()), _quiet(row_limit=2, rows_shown_to_model=2))
    with pytest.raises(ToolError):
        tools.run_sql("SELECT * FROM read_parquet('s3://cc-prod/acme_diagnostics/x.parquet')")
    with pytest.raises(ToolError):
        tools.run_sql("SELECT * FROM lossless.edi_source_file")
    assert recorded == []
    digest = tools.run_sql("SELECT claim_key, name_first, charge_amount FROM sem.claims")
    assert digest["row_count"] == 2
    assert any("LIMIT 2" in statement for statement in recorded)
    assert all(row[1] is None for row in digest["rows"])
    assert any("redacted" in warning for warning in digest["warnings"])
    assert tools.audit[-1]["customer"] == "acme_labs"
    assert "rows" not in tools.audit[-1]


def test_escalation_is_an_audited_reopen():
    plain = _claims_db([("LB1", 1.0, "Ada")])
    escalated = duckdb.connect()
    escalated.execute("CREATE SCHEMA sem")
    escalated.execute("CREATE TABLE sem.claims (claim_key VARCHAR)")
    escalated.execute("INSERT INTO sem.claims VALUES ('LB1')")
    escalated.execute("CREATE SCHEMA lossless")
    escalated.execute("CREATE TABLE lossless.edi_source_file (parse_status VARCHAR)")
    escalated.execute("INSERT INTO lossless.edi_source_file VALUES ('REJECTED')")
    opener = MemoryOpener(plain, escalated)
    tools = ToolLayer(opener, _quiet())
    with pytest.raises(ToolError):
        tools.run_sql("SELECT parse_status FROM lossless.edi_source_file")
    assert opener.opened == [False]
    granted = tools.request_escalation("count quarantined files, not in sem.*")
    assert granted["escalated"] is True and opener.opened == [False, True]
    events = [record["event"] for record in tools.audit]
    assert "escalation_requested" in events and "escalation_granted" in events
    reason = next(record for record in tools.audit if record["event"] == "escalation_requested")
    assert reason["reason"].startswith("count quarantined") and reason["user"] == "ana@xifin.example"
    got = tools.run_sql("SELECT count(*) FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'")
    assert got["rows"] == [[1]]


def test_query_time_limit():
    class Slow:
        def __init__(self):
            self.interrupted = False

        def execute(self, sql, *params):
            import time
            for _ in range(200):
                if self.interrupted:
                    raise RuntimeError("INTERRUPT")
                time.sleep(0.01)
            raise AssertionError("query was not interrupted")

        def interrupt(self):
            self.interrupted = True

    with pytest.raises(ToolError, match="time limit"):
        ToolLayer(MemoryOpener(Slow()), _quiet(time_limit_seconds=0.05)).run_sql("SELECT 1")
    fast = duckdb.connect()
    assert execute_with_time_limit(fast, "SELECT 1", 5).fetchall() == [(1,)]


def test_loop_recovers_from_sql_errors_and_empty_results():
    opener = MemoryOpener(_claims_db([("LB1", 10.0, "Ada"), ("LB2", 5.0, "Bea")]))
    errors = ScriptedLLM([
        {"tool_calls": [{"name": "run_sql", "arguments": {"sql": "SELECT no_such FROM sem.claims"}}]},
        {"tool_calls": [{"name": "run_sql", "arguments": {"sql": "SELECT count(*) AS n FROM sem.claims"}}]},
        _final("SELECT count(*) AS n FROM sem.claims", "2 claims"),
    ])
    answer = Analyzer(opener, errors, _quiet()).answer("How many distinct claims did we submit?")
    assert answer.kind == "answer" and answer.rows == [[2]]
    assert any(not step["ok"] for step in answer.trace)
    assert answer.to_final_json()["kind"] == "answer" and answer.to_final_json()["sql"].startswith("SELECT count")
    assert answer.for_judge()["cand_sql"] == answer.sql and answer.for_judge()["cand_rows"] == [[2]]
    assert errors.calls[0]["has_refusal"] and "run_sql" in errors.calls[0]["tools"]
    assert "request_escalation" in errors.calls[0]["tools"]

    empty = ScriptedLLM([
        _final("SELECT claim_key FROM sem.claims WHERE claim_key = 'missing'", "none"),
        _final("SELECT count(*) AS n FROM sem.claims", "2 claims"),
    ])
    revised = Analyzer(opener, empty, _quiet()).answer("How many distinct claims did we submit?")
    assert revised.rows == [[2]]
    assert any("no rows" in step["summary"] for step in revised.trace)


def test_candidates_vote_on_executed_results_and_cascade_on_failure():
    opener = MemoryOpener(_claims_db([("LB1", 10.0, "Ada"), ("LB2", 5.0, "Bea")]))
    voted = ScriptedLLM({
        (HAIKU_MODEL, 0): [_final("SELECT count(*) AS n FROM sem.claims", "2")],
        (HAIKU_MODEL, 1): [_final("SELECT count(claim_key) AS n FROM sem.claims", "2")],
        (HAIKU_MODEL, 2): [_final("SELECT count(*) AS n FROM sem.claims WHERE claim_key = 'nope'", "0")],
    })
    config = _quiet(candidates=3, candidate_selection="execution_vote")
    chosen = Analyzer(opener, voted, config).answer("How many claims?")
    assert chosen.rows == [[2]] and chosen.selection["candidates"] == 3

    def _split_scripts():
        return {
            (HAIKU_MODEL, 0): [_final("SELECT count(*) AS n FROM sem.claims", "2")],
            (HAIKU_MODEL, 1): [_final("SELECT count(*) AS n FROM sem.claims WHERE claim_key = 'nope'", "0")],
            (HAIKU_MODEL, 2): [_final("SELECT round(sum(charge_amount), 2) AS billed FROM sem.claims", "15")],
        }

    split = ScriptedLLM(_split_scripts())
    abstained = Analyzer(opener, split, _quiet(candidates=3, candidate_selection="agree_or_abstain")).answer("?")
    assert abstained.kind == "abstain" and abstained.to_final_json()["kind"] == "abstain"

    first = Analyzer(opener, ScriptedLLM(_split_scripts()), _quiet(candidates=3, candidate_selection="first_passing")).answer("?")
    assert first.rows == [[2]]

    cascade = ScriptedLLM({
        HAIKU_MODEL: [_final("SELECT * FROM sem.not_a_view", "no")],
        SONNET_MODEL: [_final("SELECT count(*) AS n FROM sem.claims", "2 claims")],
    })
    cascaded = Analyzer(opener, cascade, _quiet(cascade="haiku_then_sonnet", self_correct="errors_only")).answer("How many?")
    assert cascaded.model == SONNET_MODEL and cascaded.rows == [[2]]
    assert [call["model"] for call in cascade.calls][0] == HAIKU_MODEL
    refused_llm = ScriptedLLM([
        {"tool_calls": [{"name": "refuse", "arguments": {"reason": "patient identifiers are not in the session"}}]}])
    refused = Analyzer(opener, refused_llm, _quiet(cascade="haiku_then_sonnet")).answer(
        "List patient names and dates of birth")
    assert refused.kind == "refusal" and refused.refused and refused.model == HAIKU_MODEL
    assert {call["model"] for call in refused_llm.calls} == {HAIKU_MODEL}


def test_answer_scores_in_the_benchmark_format(tmp_path, monkeypatch):
    monkeypatch.setattr("bench.agent_session.SESSIONS_ROOT", tmp_path / "sessions")
    snap = _write_snapshot(tmp_path / "snap")
    opener = SnapshotOpener(snap, user="ana@xifin.example")
    llm = ScriptedLLM([_final("SELECT count(*) AS n FROM sem.claims", "2 claims")])
    answer = Analyzer(opener, llm, _quiet(verify=False, result_checks=False)).answer("How many distinct claims?")
    assert match([(2,)], [tuple(row) for row in answer.rows], {})[0]
    case = {"id": "tiny", "question": "How many distinct claims?", "category": "volume", "difficulty": "easy"}
    from bench.agent_session import create_session
    session = create_session(snap, case, {}, "scripted", "pytest-analyzer")
    (session / "final.json").write_text(json.dumps(answer.to_final_json()))
    scored = score_session(session, case, [(2,)])
    assert scored["correct"], scored

    refused = AnalyzerAnswer(kind="refusal", text="patient identifiers are not in the session", refused=True)
    session_r = create_session(snap, {**case, "id": "phi"}, {}, "scripted", "pytest-analyzer")
    (session_r / "final.json").write_text(json.dumps(refused.to_final_json()))
    assert score_session(session_r, {"expect_refusal": True}, None)["correct"]


def test_snapshot_tools_hide_lossless_until_escalation(tmp_path):
    snap = _write_snapshot(tmp_path / "snap")
    tools = ToolLayer(SnapshotOpener(snap), _quiet())
    assert tools.list_relations() == ["sem.claims"]
    sample = tools.sample_rows("sem.claims")
    assert {row[0] for row in sample["rows"]} == {"LB1", "LB2"}
    name_index = sample["columns"].index("name_first")
    assert all(row[name_index] is None for row in sample["rows"])
    with pytest.raises(ToolError):
        tools.run_sql("SELECT * FROM lossless.edi_source_file")
    tools.request_escalation("rejected-file count is only on lossless.edi_source_file")
    assert "lossless.edi_source_file" in tools.list_relations()
    counted = tools.run_sql("SELECT count(*) FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'")
    assert counted["rows"] == [[1]]


def test_prepare_can_screen_the_analyzer_prompt(tmp_path, monkeypatch):
    import bench.eval_subagents as evaluation
    root = tmp_path / "sessions"
    monkeypatch.setattr(evaluation, "SESSIONS_ROOT", root)
    monkeypatch.setattr("bench.agent_session.SESSIONS_ROOT", root)
    snap = tmp_path / "snap"
    manifest = evaluation.prepare(
        "adapt", {"winner": {"glossary": "pitfalls", "context_style": "procedural", "data_dictionary": True}},
        ["haiku"], 1, only="q01_claims_submitted", snapshot_path=snap, agent="analyzer")
    entry = json.loads(Path(manifest).read_text())["entries"][0]
    assert entry["agent"] == "analyzer"
    assert "analyzer.cli" in entry["prompt"] and "Work in these steps" in entry["prompt"]
    assert "Invariants and common traps" in entry["prompt"]
    names = [tool["name"] for tool in entry["tool_spec"]]
    assert {"run_sql", "request_escalation", "final_answer", "refuse", "lookup_dictionary"} <= set(names)
    assert "query_metrics" not in names
    cli_manifest = evaluation.prepare(
        "adapt-cli", {"base": {}}, ["haiku"], 1, only="q30_phi_names", snapshot_path=snap)
    cli_entry = json.loads(Path(cli_manifest).read_text())["entries"][0]
    assert cli_entry["agent"] == "cli" and "agent_cli.py" in cli_entry["prompt"] and "tool_spec" not in cli_entry


def test_cli_escalation_is_sticky_and_audited(tmp_path, capsys, monkeypatch):
    from analyzer.cli import main
    from bench.agent_session import create_session
    monkeypatch.setattr("bench.agent_session.SESSIONS_ROOT", tmp_path / "sessions")
    snap = _write_snapshot(tmp_path / "snap")
    session = create_session(snap, {"id": "q", "question": "How many files were rejected?"}, {}, "haiku", "pytest-cli")

    def run(*args):
        code = main(["--session", str(session), *args])
        captured = capsys.readouterr()
        return code, captured.out

    code, out = run("list")
    assert code == 0 and "lossless.edi_source_file" not in out
    assert run("sql", "SELECT * FROM lossless.edi_source_file")[0] == 1
    assert run("escalate", "rejected files are only on lossless.edi_source_file")[0] == 0
    code, out = run("sql", "SELECT count(*) FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'")
    assert code == 0 and json.loads(out)["rows"] == [[1]]
    audit = (session / "audit.jsonl").read_text()
    assert "escalation_granted" in audit and "acme_labs" in audit
    assert json.loads((session / "escalation.json").read_text())["escalated"] is True
    assert run("answer", "--sql", "SELECT count(*) FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'",
               "--text", "1 rejected file")[0] == 0
    assert json.loads((session / "final.json").read_text())["kind"] == "answer"


def test_dictionary_and_profile_land_in_the_prompt():
    opener = MemoryOpener(_claims_db([("LB1", 10.0, "Ada")]))
    llm = ScriptedLLM([_final("SELECT count(*) AS n FROM sem.claims", "1")])
    config = _quiet(glossary="pitfalls", data_dictionary=True, data_profile=True, verify=False, result_checks=False)
    answer = Analyzer(opener, llm, config).answer("How many claims?")
    assert answer.rows == [[1]]
    assert llm.calls[0]["has_pitfalls"] and llm.calls[0]["has_dictionary"] and llm.calls[0]["has_profile"]
    profile = ToolLayer(opener, config).profile("sem.claims")
    by_name = {column["name"]: column for column in profile["profiles"][0]["columns"]}
    assert by_name["name_first"]["profile"].startswith("not profiled")
    assert "values" not in by_name["claim_key"]
    assert by_name["charge_amount"]["min"] == 10.0


def _write_snapshot(path: Path) -> Path:
    path.mkdir(parents=True)
    sem = path / "data" / "sem"
    lossless = path / "data" / "lossless"
    sem.mkdir(parents=True)
    lossless.mkdir(parents=True)
    pq.write_table(pa.table({"claim_key": ["LB1", "LB2"], "charge_amount": [10.0, 5.0], "name_first": ["Ada", "Bea"]}),
                   sem / "sem.claims.parquet")
    pq.write_table(pa.table({"parse_status": ["REJECTED", "PARSED"], "file_id": ["a", "b"]}),
                   lossless / "lossless.edi_source_file.parquet")
    (path / "meta.json").write_text(json.dumps({
        "dataset_id": "tiny", "customer": "acme_labs", "gold_version": "vtest",
        "relations": ["sem.claims", "lossless.edi_source_file"]}))
    return path
