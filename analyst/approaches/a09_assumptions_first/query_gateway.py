"""SQL execution for the assumptions-first analyst.

Production opens the connection with `ccsim.query.open_session`: one per-request
DuckDB, `sem.*` always, `lossless.*` only when the session is escalated,
`allowed_directories` restricted to that customer's prefixes, external access off,
configuration locked. This gateway does not open a second path. It takes that
session and applies `bench.guard` before `execute`, the same guard the bench
uses, so a statement that is not one read-only SELECT never reaches DuckDB.
"""
from __future__ import annotations

from bench.guard import GuardError, guard


class QueryGateway:
    """Guarded `execute` over a connection that is already a customer's session."""

    def __init__(self, connection, *, escalate: bool = False, row_limit: int = 1000):
        self._connection = connection
        self.escalate = escalate
        self.row_limit = row_limit
        self.calls: list[str] = []

    def execute(self, sql: str) -> dict:
        # Imported lazily so this module and protocol.py can import each other.
        from .protocol import ProtocolError

        try:
            safe = guard(sql, self.row_limit, self.escalate)
        except GuardError as error:
            raise ProtocolError(str(error)) from None
        try:
            cursor = self._connection.execute(safe)
            columns = [description[0] for description in cursor.description]
            rows = cursor.fetchall()
        except Exception as error:  # noqa: BLE001 — the text goes back to the analyst, as the bench CLI does
            message = str(error).splitlines()[0][:300]
            raise ProtocolError(f"SQL failed: {message}") from None
        self.calls.append(sql)
        return {"columns": columns, "rows": rows, "row_count": len(rows)}


def gateway_from_query_session(session, *, escalate: bool = False) -> QueryGateway:
    """Bind a `ccsim.query.QuerySession`. The session's `.con` is the only connection used."""
    connection = getattr(session, "con", None)
    if connection is None:
        raise TypeError("expected a ccsim.query.QuerySession with a .con connection")
    return QueryGateway(connection, escalate=escalate)


def open_secure_gateway(aws, deployment, id_token: str, *, escalate: bool = False):
    """Open the production session and return `(gateway, session)`.

    `id_token` is the Cognito token `ccsim.query.login` already returned. This
    function does not sign a user in and does not take a model API key.
    """
    from ccsim.query import open_session

    session = open_session(aws, deployment, id_token, escalate=escalate)
    return gateway_from_query_session(session, escalate=escalate), session
