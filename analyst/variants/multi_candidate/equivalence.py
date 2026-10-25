"""Cluster executed results the way ``bench.compare`` decides two answers are the same.

Column names are ignored. Row order is ignored unless the question is a ranking. Numeric cells match
within the judge's absolute tolerance. A rate may match the same rate written as a percentage when the
question asks for a rate. For a top-N question, two results that differ only by which tied row they kept
at the cutoff are the same answer: each is a valid top-N of the union, which is the rule in
``bench.compare._match_topn``.

No answer key is consulted. The spec is derived from the question text (or passed in by the caller).
"""
from __future__ import annotations

import re
from collections import defaultdict

from bench.compare import (
    _eq,
    _is_numeric_col,
    _is_whole_number_col,
    _mappings,
    _match_topn,
    _num,
    _rows_equal,
    match,
)

MAX_COLUMNS = 6

_TOP_N = re.compile(
    r"\b(?:top|which|first)\s+(\d+)\b|\b(\d+)\s+(?:highest|lowest|most|least|largest|smallest)\b",
    re.IGNORECASE,
)
_RATE = re.compile(r"\b(rate|percent|percentage|share)\b", re.IGNORECASE)
_MONEY = re.compile(r"\b(dollar|amount|paid|billed|charge|interest|responsibility|deductible)\b", re.IGNORECASE)
_POSITIVE = re.compile(r"\bpositive\b", re.IGNORECASE)
_ONE_DECIMAL = re.compile(r"round(?:ed)? to one decimal", re.IGNORECASE)


def cluster_spec_for_question(question: str) -> dict:
    """Matcher options the clusterer is allowed to know. Comes from the question wording, not the answer key."""
    spec = {
        "tol": 0.005,
        "allow_percent": False,
        "allow_sign_flip": False,
        "metric_col": "auto",
    }
    if _MONEY.search(question):
        spec["tol"] = 0.01
    if _ONE_DECIMAL.search(question):
        spec["tol"] = max(spec["tol"], 0.05)
    if _RATE.search(question):
        spec["allow_percent"] = True
    if _POSITIVE.search(question):
        spec["allow_sign_flip"] = True
    found = _TOP_N.search(question)
    if found:
        spec["order"] = "topn"
        spec["n"] = int(next(group for group in found.groups() if group))
    return spec


def _as_tuples(rows) -> list[tuple]:
    out = []
    width = None
    for row in rows:
        tup = tuple(row)
        if width is None:
            width = len(tup)
        elif len(tup) != width:
            raise ValueError("ragged result cannot be clustered")
        out.append(tup)
    return out


def _directed_spec(gold: list[tuple], spec: dict) -> dict:
    """Spec for ``match`` with ``gold`` as the expected side. Percent scaling applies only to columns that
    are not whole numbers, so a count of 1 is not treated as the rate 100%."""
    directed = {"tol": spec.get("tol", 0.005)}
    if spec.get("order") == "strict":
        directed["order"] = "strict"
    if not gold:
        return directed
    if spec.get("allow_percent"):
        percent_cols = [
            j for j in range(len(gold[0]))
            if _is_numeric_col(gold, j) and not _is_whole_number_col(gold, j)
        ]
        if percent_cols:
            directed["percent_cols"] = percent_cols
    if spec.get("allow_sign_flip"):
        sign_cols = [j for j in range(len(gold[0])) if _is_numeric_col(gold, j)]
        if sign_cols:
            directed["sign_cols"] = sign_cols
    return directed


def _directed(gold, cand, spec: dict) -> bool:
    if not gold and not cand:
        return True
    if not gold or not cand or len(gold) != len(cand):
        return False
    if len(gold[0]) > MAX_COLUMNS or len(cand[0]) > MAX_COLUMNS:
        return False
    return match(gold, cand, _directed_spec(gold, spec))[0]


def _same_shape(left, right, spec: dict) -> bool:
    """Fast path: same width, cell-wise equality, no column permutation and no percent scaling."""
    if not left and not right:
        return True
    if not left or not right or len(left) != len(right) or len(left[0]) != len(right[0]):
        return False
    tol = spec.get("tol", 0.005)
    factors = [1.0] * len(left[0])
    if spec.get("order") == "strict":
        return all(all(_eq(a, b, tol, 1.0) for a, b in zip(gr, cr)) for gr, cr in zip(left, right))
    return _rows_equal(left, right, factors, tol)


def _resolve_metric_col(rows: list[tuple], spec: dict) -> int:
    raw = spec.get("metric_col", "auto")
    width = len(rows[0])
    if raw != "auto":
        return int(raw)
    fractional = [
        j for j in range(width)
        if _is_numeric_col(rows, j) and not _is_whole_number_col(rows, j)
    ]
    if len(fractional) == 1:
        return fractional[0]
    numeric = [j for j in range(width) if _is_numeric_col(rows, j)]
    return numeric[-1] if numeric else 0


def _scale_rows(rows, columns, factors) -> list[tuple]:
    scaled = []
    for row in rows:
        out = []
        for column, factor in zip(columns, factors):
            value = row[column]
            number = _num(value)
            if factor != 1.0 and number is not None and not isinstance(value, str):
                out.append(number * factor)
            else:
                out.append(value)
        scaled.append(tuple(out))
    return scaled


def _row_eq(left, right, tol: float) -> bool:
    return len(left) == len(right) and all(_eq(a, b, tol, 1.0) for a, b in zip(left, right))


def _tie_equivalent(rows_a, rows_b, metric_idx: int, n: int, tol: float, ascending: bool) -> bool:
    """Both results are valid top-N answers of their union (the judge's tie rule, applied symmetrically)."""
    if len(rows_a) != n or len(rows_b) != n:
        return False
    pool = list(rows_a)
    for row in rows_b:
        if not any(_row_eq(row, existing, tol) for existing in pool):
            pool.append(row)
    if any(_num(row[metric_idx]) is None for row in pool):
        return False
    pool.sort(key=lambda row: _num(row[metric_idx]), reverse=not ascending)
    topn_spec = {"n": n, "metric_col": metric_idx, "tol": tol, "ascending": ascending}
    return _match_topn(pool, rows_a, topn_spec)[0] and _match_topn(pool, rows_b, topn_spec)[0]


def _topn_one_way(gold, cand, spec: dict) -> bool:
    n = int(spec["n"])
    if len(gold) != n or len(cand) != n:
        return False
    metric_col = _resolve_metric_col(gold, spec)
    if not _is_numeric_col(gold, metric_col):
        return False
    directed = _directed_spec(gold, spec)
    tol = directed["tol"]
    for gold_cols, cand_cols, factors in _mappings(gold, cand, directed):
        if metric_col not in gold_cols:
            continue
        projected_gold = [tuple(row[j] for j in gold_cols) for row in gold]
        projected_cand = _scale_rows(cand, cand_cols, factors)
        metric_idx = gold_cols.index(metric_col)
        if _tie_equivalent(projected_gold, projected_cand, metric_idx, n, tol, spec.get("ascending", False)):
            return True
    return False


def _topn_equivalent(left, right, spec: dict) -> bool:
    if len(left) != len(right):
        return False
    n = int(spec["n"])
    if len(left) != n:
        return False
    return _topn_one_way(left, right, spec) or _topn_one_way(right, left, spec)


def results_equivalent(left, right, spec: dict | None = None) -> bool:
    """True when the judge would treat either result as the same answer as the other."""
    spec = dict(spec or {})
    left = _as_tuples(left)
    right = _as_tuples(right)
    if _same_shape(left, right, spec):
        return True
    order = spec.get("order")
    base = dict(spec)
    if order == "topn":
        base.pop("order", None)
    if _directed(left, right, base) or _directed(right, left, base):
        return True
    if order == "topn" and spec.get("n"):
        return _topn_equivalent(left, right, spec)
    return False


def cluster_indices(candidates: list, spec: dict | None = None) -> list[list[int]]:
    """Union-find over candidates whose ``kind`` is ``rows``. Errors and refusals are not clustered.

    Tolerance is not transitive (0 and 0.004 match, 0.004 and 0.008 match, 0 and 0.008 may not).
    The union closes that chain so a vote is not split across a rounding bridge.
    """
    spec = spec or {}
    row_indexes = [i for i, candidate in enumerate(candidates) if getattr(candidate, "kind", None) == "rows"]
    parent = {i: i for i in row_indexes}

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(a: int, b: int) -> None:
        parent[find(a)] = find(b)

    for i, left in enumerate(row_indexes):
        for right in row_indexes[i + 1:]:
            if results_equivalent(candidates[left].rows, candidates[right].rows, spec):
                union(left, right)
    groups: dict[int, list[int]] = defaultdict(list)
    for index in row_indexes:
        groups[find(index)].append(index)
    return list(groups.values())
