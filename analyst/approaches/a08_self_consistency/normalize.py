"""Canonical executed results so cosmetic differences share a vote.

Two results vote together when they are the same answer after:

- column order — text columns are identified by their values; fractional
  measures keep their relative order; whole-number measures keep theirs
  (two counts swapped is a different answer, matching `bench.compare`);
- rounding — each number is binned onto the question tolerance grid;
- ties — on `order: topn`, rows strictly better than the cutoff stay, and
  the cutoff slots become a count, so two legal tie-breaks agree.

Binning is slightly stricter than the matcher's absolute tolerance at a bin
boundary, and it is transitive. Absolute tolerance is not: 0 and 0.005 match,
0.005 and 0.010 match, 0 and 0.010 do not. A vote needs an equivalence.

Percent and sign columns follow the question spec when it declares them.
A whole-number column is rescaled as a percent only when it is the only
numeric column (25 versus 0.25). Next to a count, an integer stays an integer
so a supporting count of 86 is not divided by 100.
"""

from __future__ import annotations

import math
import re
from datetime import date, datetime
from decimal import Decimal

_MONTH = re.compile(r"^\d{4}-\d{2}$")
_NUMSTR = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_MEASURE_TEXT = re.compile(r"[.eE]")

DEFAULT_TOLERANCE = 0.005


def canonical_key(rows: list | tuple, spec: dict | None = None) -> tuple:
    """Hash key for one executed result. Empty results share one key."""
    spec = spec or {}
    tolerance = float(spec.get("tol", DEFAULT_TOLERANCE))
    materialized = [tuple(row) for row in rows]
    if not materialized:
        return ("rows", ())
    width = len(materialized[0])
    if any(len(row) != width for row in materialized):
        raise ValueError("executed rows have different widths")
    adjusted = _apply_declared_scales(materialized, tolerance, spec)
    if spec.get("order") == "topn":
        if "n" not in spec:
            raise ValueError("order topn requires n")
        return _topn_key(adjusted, tolerance, spec)
    projected = _project_table(adjusted, tolerance)
    if spec.get("order") == "strict":
        return ("strict", tuple(projected))
    return ("rows", tuple(sorted(projected, key=_row_sort_key)))


def _topn_key(rows: list[tuple], tolerance: float, spec: dict) -> tuple:
    """Identity of a top-N answer, with the cutoff tie broken out of the key."""
    requested = int(spec["n"])
    if requested <= 0:
        raise ValueError("topn n must be positive")
    take = min(requested, len(rows))
    metric_index = _metric_index(rows, spec, tolerance)
    descending = not spec.get("ascending", False)

    def metric(row):
        number = _number(row[metric_index])
        if number is None:
            return None
        return _bin_number(number, tolerance)

    def sort_metric(row):
        value = metric(row)
        if value is None:
            return (1, 0.0)
        return (0, -value if descending else value)

    head = sorted(rows, key=sort_metric)[:take]
    cutoff = metric(head[-1])
    better = [row for row in head if metric(row) != cutoff]
    tied = [row for row in head if metric(row) == cutoff]
    plan = _column_plan(head, tolerance)
    better_projected = sorted((_project_row(row, plan, tolerance) for row in better), key=_row_sort_key)
    kinds = tuple(kind for kind, _index in plan)
    return ("topn", take, kinds, tuple(better_projected), cutoff, len(tied))


def _metric_index(rows: list[tuple], spec: dict, tolerance: float) -> int:
    numeric = [index for index in range(len(rows[0])) if _column_numbers(rows, index, tolerance) is not None]
    requested = spec.get("metric_col")
    if isinstance(requested, int) and requested in numeric:
        return requested
    if len(numeric) == 1:
        return numeric[0]
    if numeric:
        return numeric[-1]
    raise ValueError("topn result has no numeric column to rank on")


def _apply_declared_scales(rows: list[tuple], tolerance: float, spec: dict) -> list[tuple]:
    if not rows:
        return rows
    width = len(rows[0])
    mutable = [list(row) for row in rows]
    numeric_indexes = [index for index in range(width) if _column_numbers(rows, index, tolerance) is not None]
    if spec.get("percent_cols"):
        for index in numeric_indexes:
            values = _column_numbers(rows, index, tolerance)
            if values is None:
                raise RuntimeError(f"column {index} was classified numeric and then was not")
            if any(abs(value) > 100 + tolerance for value in values):
                continue
            if all(abs(value) <= 1 + tolerance for value in values):
                continue
            near_integer = all(_near_integer(value, tolerance) for value in values)
            if near_integer and len(numeric_indexes) > 1:
                continue
            for row in mutable:
                number = _number(row[index])
                if number is not None:
                    row[index] = number / 100.0
    if spec.get("sign_cols"):
        for index in numeric_indexes:
            values = []
            for row in mutable:
                number = _number(row[index])
                if number is not None:
                    values.append(number)
            column_is_non_positive = bool(values) and all(value <= tolerance for value in values)
            column_has_a_credit = any(value < -tolerance for value in values)
            if column_is_non_positive and column_has_a_credit:
                for row in mutable:
                    number = _number(row[index])
                    if number is not None:
                        row[index] = -number
    return [tuple(row) for row in mutable]


def _project_table(rows: list[tuple], tolerance: float) -> list[tuple]:
    plan = _column_plan(rows, tolerance)
    return [_project_row(row, plan, tolerance) for row in rows]


def _column_plan(rows: list[tuple], tolerance: float) -> list[tuple[str, int]]:
    """Text columns sorted by content; each numeric kind keeps its relative order."""
    text_indexes, fraction_indexes, whole_indexes = [], [], []
    for index in range(len(rows[0])):
        values = _column_numbers(rows, index, tolerance)
        if values is None:
            text_indexes.append(index)
        elif all(_near_integer(value, tolerance) for value in values):
            whole_indexes.append(index)
        else:
            fraction_indexes.append(index)
    text_indexes.sort(key=lambda index: _text_signature(rows, index))
    return (
        [("text", index) for index in text_indexes]
        + [("fraction", index) for index in fraction_indexes]
        + [("whole", index) for index in whole_indexes]
    )


def _project_row(row: tuple, plan: list[tuple[str, int]], tolerance: float) -> tuple:
    projected = []
    for kind, index in plan:
        value = row[index]
        if kind == "text":
            projected.append(("t", _text(value)))
            continue
        number = _number(value)
        if number is None:
            projected.append(("w" if kind == "whole" else "f", None))
            continue
        binned = _bin_number(number, tolerance)
        if kind == "whole":
            projected.append(("w", int(round(binned))))
        else:
            projected.append(("f", binned))
    return tuple(projected)


def _column_numbers(rows: list[tuple], index: int, tolerance: float) -> list[float] | None:
    """Numbers in this column, or None when the column is text.

    Integer-like strings (CPT `80053`) stay text, so they reorder with labels.
    A decimal string (`0.250`) is a measure written as text and is rounded
    with the numbers. `tolerance` is unused; column kind uses the raw values
    and binning happens later. It is accepted so callers can pass it uniformly.
    """
    del tolerance
    present = [row[index] for row in rows if row[index] is not None]
    if not present:
        return None
    if not all(_number(value) is not None for value in present):
        return None
    string_values = [value for value in present if isinstance(value, str)]
    if string_values and not any(not isinstance(value, str) for value in present):
        if not any(_MEASURE_TEXT.search(value) for value in string_values):
            return None
    return [float(_number(value)) for value in present]


def _text_signature(rows: list[tuple], index: int) -> tuple:
    return tuple(sorted(_text(row[index]) or "" for row in rows))


def _number(value):
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float, Decimal)):
        as_float = float(value)
        if math.isnan(as_float) or math.isinf(as_float):
            return None
        return as_float
    if isinstance(value, str) and _NUMSTR.match(value.strip()):
        return float(value.strip())
    return None


def _text(value) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return str(value)[:10]
    if isinstance(value, str):
        stripped = value.strip()
        if _MONTH.match(stripped):
            return stripped + "-01"
        return stripped.lower()
    if isinstance(value, (list, tuple)):
        return "|".join(sorted(str(part) for part in value))
    return str(value).strip().lower()


def _near_integer(number: float, tolerance: float) -> bool:
    return abs(number - round(number)) <= tolerance + 1e-9


def _bin_number(number: float, tolerance: float) -> float:
    if tolerance <= 0:
        binned = number
    else:
        binned = round(number / tolerance) * tolerance
    if abs(binned) < 1e-12:
        return 0.0
    return round(binned, 10)


def _row_sort_key(row: tuple) -> tuple:
    """Order a multiset without comparing None to a string."""
    keyed = []
    for cell in row:
        tag, value = cell
        keyed.append((tag, value is None, "" if value is None else value))
    return tuple(keyed)
