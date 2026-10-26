"""SQL execution for A05.

Scored bench runs go through bench.agent_cli on the session snapshot. That sandbox is the
harness copy of the production session: one read-only SELECT, sem.* unless the question
escalates, external access off, configuration locked.

A live deployment already holds a ccsim.query.QuerySession (IAM role, secret scope,
allowed_directories, locked configuration). execute_on_query_session runs the same guard
on that connection. It does not open a second connection and it does not widen the session.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from bench.guard import GuardError, guard

GUARD_ROW_LIMIT = 1000


@dataclass
class QueryResult:
    ok: bool
    sql: str
    columns: list[str] | None = None
    rows: list[list] = field(default_factory=list)
    error: str | None = None


class QueryExecutor(Protocol):
    def schema_context(self) -> str: ...

    def execute(self, sql: str) -> QueryResult: ...

    def submit_answer(self, sql: str, text: str) -> None: ...

    def submit_refusal(self, reason: str) -> None: ...


def execute_on_connection(connection, sql: str, *, escalate: bool = False) -> QueryResult:
    """Guard first. A rejected statement never reaches DuckDB."""
    try:
        safe_sql = guard(sql, GUARD_ROW_LIMIT, escalate)
    except GuardError as exc:
        return QueryResult(ok=False, sql=sql, error=str(exc))
    try:
        cursor = connection.execute(safe_sql)
        columns = [description[0] for description in cursor.description]
        rows = [list(row) for row in cursor.fetchall()]
    except Exception as exc:  # noqa: BLE001 - the text goes back to the analyst for the one repair
        return QueryResult(ok=False, sql=sql, error=str(exc).splitlines()[0][:400])
    return QueryResult(ok=True, sql=sql, columns=columns, rows=rows)


def execute_on_query_session(session, sql: str, *, escalate: bool = False) -> QueryResult:
    """Run one guarded SELECT on a ccsim.query.QuerySession (or any object with .con)."""
    return execute_on_connection(session.con, sql, escalate=escalate)


class ConnectionExecutor:
    """In-memory or already-open DuckDB. Tests use this; it still goes through the guard."""

    def __init__(self, connection, schema_context: str, *, escalate: bool = False):
        self._connection = connection
        self._schema_context = schema_context
        self.escalate = escalate
        self.submitted: tuple | None = None

    def schema_context(self) -> str:
        return self._schema_context

    def execute(self, sql: str) -> QueryResult:
        return execute_on_connection(self._connection, sql, escalate=self.escalate)

    def submit_answer(self, sql: str, text: str) -> None:
        self.submitted = ("answer", sql, text)

    def submit_refusal(self, reason: str) -> None:
        self.submitted = ("refusal", reason)


class BenchSandboxExecutor:
    """The eval session: agent_cli handlers, so calls.jsonl and final.json match the scorer."""

    def __init__(self, session_dir):
        from pathlib import Path

        from bench.agent_cli import Session

        self.session = Session(Path(session_dir))

    def schema_context(self) -> str:
        from bench.agent_cli import command_context

        text = command_context(self.session)
        self.session.log("context", {}, True, "ok")
        return text

    def execute(self, sql: str) -> QueryResult:
        from bench.agent_cli import CliError, command_sql

        try:
            payload = command_sql(self.session, sql)
        except CliError as exc:
            self.session.log("sql", {"query": sql}, False, str(exc)[:300])
            return QueryResult(ok=False, sql=sql, error=str(exc))
        rows = [list(row) for row in payload["rows"]]
        self.session.log("sql", {"query": sql}, True, {"row_count": payload["row_count"]})
        return QueryResult(ok=True, sql=sql, columns=list(payload["columns"]), rows=rows)

    def submit_answer(self, sql: str, text: str) -> None:
        from bench.agent_cli import command_answer

        command_answer(self.session, sql, None, text)
        self.session.log("answer", {"sql": sql}, True, "ok")

    def submit_refusal(self, reason: str) -> None:
        from bench.agent_cli import command_refuse

        command_refuse(self.session, reason)
        self.session.log("refuse", {"reason": reason}, True, "ok")
