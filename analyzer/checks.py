"""Sanity checks on a successful query.

The warning text is the bench's, so a screening run and an in-process run flag
the same traps (empty results, rates outside 0..100, missing current-adjudication
or latest-submission filters, repeated group keys). Blocking warnings are the
ones the loop sends back for a revision: an empty result, or a rate that cannot
be a percentage. Grain warnings stay advisory so a question that really wants
every 835 event is not rejected forever.
"""

from __future__ import annotations

import re

from bench.agent_cli import PITFALLS, _result_warnings, _sql_error_hint

__all__ = ["PITFALLS", "blocking_warnings", "result_warnings", "sql_error_message"]

_BLOCKING = re.compile(r"no rows|outside 0\.\.100", re.IGNORECASE)


def result_warnings(sql: str, columns, rows, enabled: bool) -> list[str]:
    if not enabled:
        return []
    return _result_warnings(sql, columns, rows)


def blocking_warnings(warnings: list[str]) -> list[str]:
    return [warning for warning in warnings if _BLOCKING.search(warning)]


def sql_error_message(exc: BaseException, hints_enabled: bool) -> str:
    message = str(exc).splitlines()[0][:500]
    if hints_enabled:
        message += _sql_error_hint(message)
    return message
