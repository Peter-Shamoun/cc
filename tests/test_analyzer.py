"""The analyzer's tool layer: guarded SQL, limits, PHI redaction, query-time budget, audited escalation."""
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from analyzer.config import AnalyzerConfig
from analyzer.opener import SessionHandle, SnapshotOpener
from analyzer.tools import MAX_TOTAL_QUERY_SECONDS_PER_SESSION, ToolError, ToolLayer, execute_with_time_limit


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
    return AnalyzerConfig(**overrides)


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


def test_run_sql_refuses_a_cartesian_join_before_running_it_and_says_what_to_change():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA sem")
    con.execute("CREATE TABLE sem.claims AS SELECT 'C' || i AS claim_key, i * 1.0 AS charge_amount FROM range(20000) t(i)")
    executed = []

    class Recording:
        def execute(self, sql, *params):
            executed.append(sql)
            return con.execute(sql, *params)

        def interrupt(self):
            con.interrupt()

    tools = ToolLayer(MemoryOpener(Recording()), _quiet())
    with pytest.raises(ToolError) as refused:
        tools.run_sql("SELECT count(*) FROM sem.claims a, sem.claims b")
    assert "too expensive" in str(refused.value) and "Join on a shared key" in str(refused.value)
    assert all(statement.startswith("EXPLAIN") for statement in executed)
    assert tools.audit[-1]["ok"] is False and "too expensive" in tools.audit[-1]["error"]
    keyed = tools.run_sql("SELECT count(*) FROM sem.claims a JOIN sem.claims b USING (claim_key)")
    assert keyed["rows"] == [[20000]]


def test_query_time_per_session_is_capped_and_counted():
    con = _claims_db([("LB1", 10.0, "Ada")])
    tools = ToolLayer(MemoryOpener(con), _quiet())
    tools.run_sql("SELECT count(*) FROM sem.claims")
    with pytest.raises(ToolError):
        tools.run_sql("SELECT * FROM sem.not_a_view")
    assert tools.query_seconds_used > 0
    assert all("seconds" in record for record in tools.audit if record["event"] == "sql")
    spent = ToolLayer(MemoryOpener(con), _quiet())
    spent.query_seconds_used = MAX_TOTAL_QUERY_SECONDS_PER_SESSION
    with pytest.raises(ToolError) as exhausted:
        spent.run_sql("SELECT count(*) FROM sem.claims")
    assert f"{MAX_TOTAL_QUERY_SECONDS_PER_SESSION:g} s of query time" in str(exhausted.value)
    assert "Refuse" in str(exhausted.value)


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


def test_snapshot_tools_hide_lossless_until_escalation(tmp_path):
    snap = _write_snapshot(tmp_path / "snap")
    tools = ToolLayer(SnapshotOpener(snap), _quiet())
    assert tools.list_relations() == ["sem.claims"]
    sample = tools.run_sql("SELECT * FROM sem.claims")
    assert {row[0] for row in sample["rows"]} == {"LB1", "LB2"}
    name_index = sample["columns"].index("name_first")
    assert all(row[name_index] is None for row in sample["rows"])
    with pytest.raises(ToolError):
        tools.run_sql("SELECT * FROM lossless.edi_source_file")
    tools.request_escalation("rejected-file count is only on lossless.edi_source_file")
    assert "lossless.edi_source_file" in tools.list_relations()
    counted = tools.run_sql("SELECT count(*) FROM lossless.edi_source_file WHERE parse_status = 'REJECTED'")
    assert counted["rows"] == [[1]]


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
