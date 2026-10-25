"""Execution adapter. SQL runs only through ``ccsim.query.open_session`` and ``bench.guard``.

The agent never sees the DuckDB connection, the manifest, or a path. ``describe`` / ``sql`` is the
same surface ``bench.agents.Tools`` gives an in-process agent; the connection underneath is the
production reader session rather than a snapshot.
"""
from __future__ import annotations

import time

from bench.agents import GLOSSARY
from bench.guard import guard
from ccsim.query import open_session


class SecureQueryTools:
    def __init__(self, aws, deployment, id_token: str, escalate: bool = False, row_limit: int = 1000):
        self.session = open_session(aws, deployment, id_token, escalate=escalate)
        self.escalate = escalate
        self.row_limit = row_limit
        self.calls: list[dict] = []

    def describe(self) -> str:
        lines = []
        connection = self.session.con
        for schema, view in connection.execute(
            "SELECT schema_name, view_name FROM duckdb_views() WHERE NOT internal ORDER BY 1, 2"
        ).fetchall():
            cols = connection.execute(f"DESCRIBE {schema}.{view}").fetchall()
            lines.append(f"{schema}.{view}(" + ", ".join(f"{c[0]} {c[1]}" for c in cols) + ")")
        return "\n".join(lines) + "\n\n" + GLOSSARY

    def sql(self, query: str, _final: bool = False):
        started = time.perf_counter()
        try:
            safe = guard(query, self.row_limit, self.escalate)
            cursor = self.session.con.execute(safe)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            rows = cursor.fetchall()
            self.calls.append(dict(sql=query, ok=True, rows=len(rows), s=round(time.perf_counter() - started, 4)))
            return columns, rows
        except Exception as exc:
            self.calls.append(dict(sql=query, ok=False, error=str(exc)[:300], s=round(time.perf_counter() - started, 4)))
            raise
