"""Programmatic signals on a query that already executed.

These are the repair triggers for empty-adjacent and suspicious results. An
execution error is classified by the caller, not here. The signals match the
warnings in bench/agent_cli.py, read off the SQL text and the rows, so this
package does not import that private helper. They are checks, not a second model.
"""

from __future__ import annotations

import numbers
import re

_RATE_NAME = re.compile(r"rate|pct|percent|share", re.IGNORECASE)
_LINE_COMMENT = re.compile(r"--[^\n]*")
_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_STRING_LITERAL = re.compile(r"'(?:''|[^'])*'")
_CLAIMS_RELATION = re.compile(r"\bsem\.claims\b|\bsem\.claim_lines\b")
_REMIT_CLAIMS_RELATION = re.compile(r"\bsem\.remit_claims\b")

# Compared only when both columns are present, so an unrelated amount cannot trip them.
_NUMERATOR_DENOMINATOR_COLUMNS = (("denied_claims", "remitted_claims"), ("numerator", "denominator"))


def sql_text_for_scans(sql: str) -> str:
    """Lowercased SQL with comments and string literals removed, so a comment cannot hide a missing filter."""
    without_comments = _LINE_COMMENT.sub(" ", _BLOCK_COMMENT.sub(" ", sql))
    return _STRING_LITERAL.sub("''", without_comments).lower()


def _as_float(value) -> float | None:
    if isinstance(value, bool) or not isinstance(value, numbers.Number):
        return None
    return float(value)


def suspicious_result_signals(sql: str, columns: list[str], rows: list) -> list[str]:
    """Signals that should send the draft back for repair. Empty means the rows looked usable."""
    signals = []
    scanned = sql_text_for_scans(sql)
    if _CLAIMS_RELATION.search(scanned) and "is_latest_submission" not in scanned:
        signals.append(
            "sem.claims or sem.claim_lines is used without is_latest_submission, "
            "so resubmissions and secondary bills can be counted again"
        )
    if _REMIT_CLAIMS_RELATION.search(scanned) and "is_current_adjudication" not in scanned:
        signals.append(
            "sem.remit_claims is used without is_current_adjudication, "
            "so reversals and superseded payments are included"
        )
    if not rows:
        return signals
    for index, name in enumerate(columns):
        values = [row[index] for row in rows]
        if all(value is None for value in values):
            signals.append(f"column {name} is NULL in every returned row")
        if _RATE_NAME.search(str(name)):
            numbers_in_column = [number for number in (_as_float(value) for value in values) if number is not None]
            if any(number < 0 or number > 100 for number in numbers_in_column):
                signals.append(f"column {name} has a value outside 0..100")
    column_index = {str(name): index for index, name in enumerate(columns)}
    for numerator_name, denominator_name in _NUMERATOR_DENOMINATOR_COLUMNS:
        if numerator_name not in column_index or denominator_name not in column_index:
            continue
        numerator_index = column_index[numerator_name]
        denominator_index = column_index[denominator_name]
        for row in rows:
            numerator = _as_float(row[numerator_index])
            denominator = _as_float(row[denominator_index])
            if numerator is not None and denominator is not None and numerator > denominator:
                signals.append(f"{numerator_name} is greater than {denominator_name}")
                break
    if len(rows) > 1 and len(columns) > 1:
        try:
            distinct_first_column = len({row[0] for row in rows})
        except TypeError:
            distinct_first_column = len(rows)
        if distinct_first_column < len(rows):
            signals.append(
                f"the first column ({columns[0]}) repeats across rows; the grain may have fanned out"
            )
    return signals
