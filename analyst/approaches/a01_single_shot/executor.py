"""Execute the one submitted statement on a session that already matches ``ccsim/query.py``.

Production opens that session with ``ccsim.query.open_session``: the customer's reader role,
``sem.*`` only unless the question escalates, ``allowed_directories`` limited to those
prefixes, external access off, configuration locked. The bench freezes the same session as
local Parquet and reopens it with ``bench.dataset.open_snapshot``, which sets the same
restrictions. A01 does not open a second connection and does not query outside ``guard``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from bench.guard import guard

from .dictionary import build_data_dictionary

ROW_LIMIT = 1000


@dataclass
class QueryResult:
    columns: list[str]
    rows: list


def execute_guarded(con, sql: str, escalate: bool) -> QueryResult:
    """Run ``guard`` and then exactly one statement. ``guard`` rejects anything but a single read-only SELECT."""
    safe = guard(sql, ROW_LIMIT, escalate)
    cursor = con.execute(safe)
    columns = [description[0] for description in cursor.description]
    return QueryResult(columns=columns, rows=cursor.fetchall())


class ConnectionQueryExecutor:
    """Guarded execution on a connection the caller already opened. Tests use this with an in-memory database."""

    def __init__(self, con, escalate: bool = False, model_path: Path | None = None):
        self.con = con
        self.escalate = escalate
        self.model_path = model_path
        self.executed_sql: list[str] = []

    def dictionary(self) -> str:
        return build_data_dictionary(self.con, self.model_path)

    def execute(self, sql: str) -> QueryResult:
        self.executed_sql.append(sql)
        return execute_guarded(self.con, sql, self.escalate)


class SnapshotQueryExecutor:
    """The bench form of a ``ccsim.query`` session: one sandboxed connection for the whole question."""

    def __init__(self, snapshot_path: Path, escalate: bool = False, model_path: Path | None = None):
        self.snapshot_path = Path(snapshot_path)
        self.escalate = escalate
        self.model_path = model_path
        self._con = None
        self.executed_sql: list[str] = []

    @property
    def con(self):
        if self._con is None:
            from bench.dataset import open_snapshot
            self._con = open_snapshot(self.snapshot_path, escalate=self.escalate)
        return self._con

    def dictionary(self) -> str:
        return build_data_dictionary(self.con, self.model_path)

    def execute(self, sql: str) -> QueryResult:
        self.executed_sql.append(sql)
        return execute_guarded(self.con, sql, self.escalate)
