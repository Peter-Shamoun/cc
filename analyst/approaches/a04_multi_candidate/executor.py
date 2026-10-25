"""Execute one candidate on the secure read path.

`ccsim.query.open_session` is what a request runs in production: a fresh DuckDB, secrets and
allowed directories limited to that customer's `sem.*` (and `lossless.*` only when escalating),
external access off, configuration locked. That session does not parse the agent's SQL. The
bench guard does, and every candidate goes through it before it touches `QuerySession.con`.

`ToolsExecutor` is the same guard via `bench.agents.Tools.sql`, which is how `bench.eval`
already reaches a snapshot of those views. A SELECT that names no schema-qualified table is
rejected here, matching the harness rule that a final answer has to be grounded in the data.
"""
from __future__ import annotations

from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from bench.guard import guard


@dataclass
class Execution:
    columns: tuple = ()
    rows: tuple = ()
    error: str | None = None


def reads_schema_table(sql: str) -> bool:
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return False
    return any(table.db for table in tree.find_all(exp.Table))


def rejection(sql: str, escalate: bool, row_limit: int) -> str | None:
    """None when the SQL may run. The guard error wins over the grounding error."""
    try:
        guard(sql, row_limit, escalate)
    except Exception as e:  # noqa: BLE001 — GuardError and parse failures become a candidate error
        return _short(e)
    if not reads_schema_table(sql):
        return "query reads no schema-qualified table"
    return None


class GuardedExecutor:
    """`connection` is a `ccsim.query.QuerySession.con`, or a DuckDB connection in a test."""

    def __init__(self, connection, escalate: bool = False, row_limit: int = 1000):
        self.connection = connection
        self.escalate = escalate
        self.row_limit = row_limit

    def execute(self, sql: str) -> Execution:
        error = rejection(sql, self.escalate, self.row_limit)
        if error:
            return Execution(error=error)
        try:
            cursor = self.connection.execute(guard(sql, self.row_limit, self.escalate))
            columns = tuple(d[0] for d in cursor.description)
            rows = tuple(tuple(row) for row in cursor.fetchall())
        except Exception as e:  # noqa: BLE001 — a bad candidate must not abort the other two
            return Execution(error=_short(e))
        return Execution(columns=columns, rows=rows)


class ToolsExecutor:
    """Bench path. `tools.sql` applies the same guard and records the call on `tools.calls`."""

    def __init__(self, tools):
        self.tools = tools

    def execute(self, sql: str) -> Execution:
        # tools.sql is the bench's guarded path and records the call, including failures.
        # A constant SELECT passes that guard, then fails the grounding check below.
        try:
            columns, rows = self.tools.sql(sql)
        except Exception as e:  # noqa: BLE001
            return Execution(error=_short(e))
        if not reads_schema_table(sql):
            return Execution(error="query reads no schema-qualified table")
        return Execution(columns=tuple(columns), rows=tuple(tuple(row) for row in rows))


def executor_for_query_session(session, escalate: bool = False, row_limit: int = 1000) -> GuardedExecutor:
    """Bind A04 to one `ccsim.query.QuerySession`. Does not open a session or call AWS."""
    return GuardedExecutor(session.con, escalate=escalate, row_limit=row_limit)


def _short(error: BaseException) -> str:
    return str(error).splitlines()[0][:400]
