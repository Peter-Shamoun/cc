"""Intermediate checks for one CTE step: row count, grain uniqueness, preserved sums.

The functions take the one aggregate row a check query returns. They do not open
a database connection. Ranking order is checked on the measure values in result
order, which is a separate small fetch because an aggregate cannot show order.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SUM_TOLERANCE = 0.02


class CheckError(ValueError):
    """The check inputs themselves are invalid (not a failed business check)."""


@dataclass
class CheckFailure:
    code: str
    message: str


@dataclass
class CheckReport:
    role: str
    cte_name: str
    ok: bool
    row_count: int | None = None
    distinct_grain: int | None = None
    null_grain_rows: int | None = None
    sums: dict = field(default_factory=dict)
    parent_sums: dict = field(default_factory=dict)
    failures: list[CheckFailure] = field(default_factory=list)

    def failure_text(self) -> str:
        if not self.failures:
            return "no failures"
        return "\n".join(f"{item.code}: {item.message}" for item in self.failures)


def require_identifier(name: str, what: str) -> str:
    if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
        raise CheckError(f"{what} {name!r} must be a plain identifier")
    return name


def quote_identifier(name: str) -> str:
    return '"' + require_identifier(name, "identifier") + '"'


def sum_alias(column: str) -> str:
    return "sum_" + require_identifier(column, "sum column")


def aggregate_select_list(grain_keys: tuple[str, ...], preserve_sums: tuple[str, ...], cte_name: str) -> str:
    """SELECT list for the one-row check of `cte_name`. Names are quoted identifiers."""
    quoted_cte = quote_identifier(cte_name)
    parts = ["count(*) AS row_count"]
    if grain_keys:
        columns = ", ".join(quote_identifier(key) for key in grain_keys)
        parts.append(
            f"(SELECT count(*) FROM (SELECT DISTINCT {columns} FROM {quoted_cte})) AS distinct_grain"
        )
        null_predicate = " OR ".join(f"{quote_identifier(key)} IS NULL" for key in grain_keys)
        parts.append(f"count(*) FILTER (WHERE {null_predicate}) AS null_grain_rows")
    for column in preserve_sums:
        parts.append(f"sum({quote_identifier(column)}) AS {sum_alias(column)}")
    return ", ".join(parts)


def values_are_monotonic(values: list, direction: str) -> bool:
    """True when `values` are sorted. Equal neighbors (ties) are allowed. NULL is not."""
    if direction not in ("asc", "desc"):
        raise CheckError(f"order direction must be asc or desc, got {direction!r}")
    converted = []
    for value in values:
        if value is None:
            return False
        converted.append(_sortable(value))
    for earlier, later in zip(converted, converted[1:]):
        if direction == "desc" and later > earlier:
            return False
        if direction == "asc" and later < earlier:
            return False
    return True


def report_from_aggregate(
    *,
    role: str,
    cte_name: str,
    columns: list[str],
    rows: list[tuple],
    grain_keys: tuple[str, ...],
    preserve_sums: tuple[str, ...],
    expect_nonempty: bool,
    max_rows: int | None,
    max_fanout: float | None,
    parent_row_count: int | None,
    parent_sums: dict[str, float | None],
    tolerance: float = SUM_TOLERANCE,
) -> CheckReport:
    """Turn the aggregate check row into a pass or a list of failures."""
    failures: list[CheckFailure] = []
    if len(rows) != 1:
        failures.append(CheckFailure("execution_error", f"check query returned {len(rows)} rows, expected 1"))
        return CheckReport(role=role, cte_name=cte_name, ok=False, failures=failures)

    row = rows[0]
    row_count = _required_int(columns, row, "row_count", failures)
    distinct_grain = _named_int(columns, row, "distinct_grain", failures) if grain_keys else None
    null_grain_rows = _named_int(columns, row, "null_grain_rows", failures) if grain_keys else None
    sums = {}
    for column in preserve_sums:
        sums[column] = _optional_float(columns, row, sum_alias(column), failures, column)

    if row_count is not None and expect_nonempty and row_count == 0:
        failures.append(CheckFailure("empty", "the step returned no rows"))
    if row_count is not None and max_rows is not None and row_count > max_rows:
        failures.append(CheckFailure("too_many_rows", f"row_count {row_count} is above the limit {max_rows}"))
    if grain_keys and distinct_grain is not None and row_count is not None and distinct_grain != row_count:
        failures.append(CheckFailure(
            "duplicate_grain",
            f"distinct {', '.join(grain_keys)} is {distinct_grain} and row_count is {row_count}",
        ))
    if grain_keys and null_grain_rows:
        failures.append(CheckFailure("null_grain", f"{null_grain_rows} rows have a NULL grain key"))
    if max_fanout is not None:
        _check_fanout(max_fanout, row_count, parent_row_count, failures)
    for column in preserve_sums:
        if column not in parent_sums:
            continue
        current = sums.get(column)
        previous = parent_sums[column]
        if not _sums_match(current, previous, tolerance):
            failures.append(CheckFailure(
                "sum_mismatch",
                f"sum of {column} is {current} and the previous step summed to {previous}",
            ))

    return CheckReport(
        role=role, cte_name=cte_name, ok=not failures, row_count=row_count,
        distinct_grain=distinct_grain, null_grain_rows=null_grain_rows, sums=sums,
        parent_sums={key: parent_sums.get(key) for key in preserve_sums}, failures=failures,
    )


def monotonic_failure(values: list, direction: str, column: str) -> CheckFailure | None:
    if values_are_monotonic(values, direction):
        return None
    shown = ", ".join(repr(value) for value in values[:8])
    return CheckFailure("not_monotonic", f"{column} is not {direction} in result order: {shown}")


def _check_fanout(max_fanout: float, row_count: int | None, parent_row_count: int | None, failures: list[CheckFailure]):
    if row_count is None or parent_row_count is None:
        failures.append(CheckFailure("fanout_unknown", "fan-out was requested but a row count is missing"))
        return
    if parent_row_count == 0:
        return
    ratio = row_count / parent_row_count
    if ratio > max_fanout:
        failures.append(CheckFailure(
            "fanout",
            f"row_count grew by {ratio:.2f} times the previous step, above max_fanout {max_fanout}",
        ))


def _sums_match(current: float | None, previous: float | None, tolerance: float) -> bool:
    if current is None and previous is None:
        return True
    if current is None or previous is None:
        return False
    return abs(current - previous) <= tolerance


def _sortable(value):
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, Decimal):
        return float(value)
    return value


def _required_int(columns, row, name, failures: list[CheckFailure]) -> int | None:
    present, value = _cell(columns, row, name)
    if not present:
        failures.append(CheckFailure("execution_error", f"check result has no {name} column"))
        return None
    if value is None:
        failures.append(CheckFailure("execution_error", f"{name} is NULL"))
        return None
    return int(value)


def _named_int(columns, row, name, failures: list[CheckFailure]) -> int | None:
    present, value = _cell(columns, row, name)
    if not present or value is None:
        failures.append(CheckFailure("execution_error", f"check result has no {name}"))
        return None
    return int(value)


def _optional_float(columns, row, alias, failures: list[CheckFailure], column: str) -> float | None:
    present, value = _cell(columns, row, alias)
    if not present:
        failures.append(CheckFailure("execution_error", f"check result has no sum of {column}"))
        return None
    if value is None:
        return None
    return float(value)


def _cell(columns: list[str], row: tuple, name: str):
    folded = {column.lower(): index for index, column in enumerate(columns)}
    index = folded.get(name.lower())
    if index is None:
        return False, None
    return True, row[index]
