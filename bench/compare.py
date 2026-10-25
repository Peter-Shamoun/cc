"""Deterministic answer matching.

A candidate result is correct when it states the expected answer and nothing that contradicts it:

- Columns are matched by value, not name (agents name columns freely). Every numeric column the candidate
  returns must map to an expected column, so a result that lists several alternative numbers fails, with one
  exception: whole-number supporting counts beside fractional measures (rates, averages, amounts). Extra text
  columns (labels, names next to ids) are allowed. Expected columns are required unless the question marks
  them `optional_cols` (e.g. a supporting count).
- Measures keep the expected column order, so two counts cannot be swapped.
- Rows must match as a multiset within `tol`. `order: strict` also checks order. `order: topn` (with `n` and
  `metric_col`) accepts any valid top-N when there are ties at the cut-off.
- `percent_cols` and `sign_cols` allow, per listed column and for the whole column at once, a rate given as a
  percentage or a credit given with the opposite sign.
- Booleans count as 0/1, numeric strings as numbers, a 'YYYY-MM' month as that month's first day.

Anything this rejects can still be appealed to the LLM judge, which sees both results.
"""
from __future__ import annotations

import itertools
import math
import re
from datetime import date, datetime
from decimal import Decimal

_MONTH = re.compile(r"^\d{4}-\d{2}$")
_NUMSTR = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")


def _num(v):
    if isinstance(v, bool):
        return float(v)
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return None if math.isnan(f) else f
    if isinstance(v, str) and _NUMSTR.match(v.strip()):
        return float(v)
    return None


def _text(v) -> str | None:
    if v is None:
        return None
    if isinstance(v, (date, datetime)):
        return str(v)[:10]
    if isinstance(v, str) and _MONTH.match(v.strip()):
        return v.strip() + "-01"
    if isinstance(v, (list, tuple)):
        return "|".join(sorted(map(str, v)))
    return str(v).strip().lower()


def _is_numeric_col(rows, j) -> bool:
    vals = [r[j] for r in rows if r[j] is not None]
    return bool(vals) and all(_num(v) is not None and not isinstance(v, str) for v in vals)


def _eq(g, c, tol, factor) -> bool:
    gn, cn = _num(g), _num(c)
    if gn is not None and cn is not None and not isinstance(g, str):     # expected text never compares as a number
        return abs(gn - cn * factor) <= tol + 1e-12
    if g is None or c is None:
        return g is None and c is None
    return _text(g) == _text(c)


def _rows_equal(gold_rows, cand_rows, factors, tol) -> bool:
    """Multiset equality of (already projected) rows; greedy is exact here because equality is per cell."""
    remaining = list(cand_rows)
    for gr in gold_rows:
        hit = next((i for i, cr in enumerate(remaining)
                    if all(_eq(g, c, tol, f) for g, c, f in zip(gr, cr, factors))), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return not remaining


def _is_whole_number_col(rows, j) -> bool:
    vals = [_num(r[j]) for r in rows if r[j] is not None]
    return bool(vals) and all(v is not None and float(v).is_integer() for v in vals)


def _supporting_counts_allowed(gold, cand, gcols, g_num, unexplained, spec) -> bool:
    """Extra numeric columns are tolerated only as supporting counts next to fractional measures (a rate, an
    average, an amount): every extra column holds whole numbers and every expected measure is fractional. An extra
    rate beside a rate, or an extra count beside a count, could be an alternative answer, so it fails."""
    if spec.get("strict_extra_columns"):
        return False
    measures = [g for g in gcols if g in g_num]
    return bool(measures) and all(_is_whole_number_col(cand, j) for j in unexplained) and \
        not any(_is_whole_number_col(gold, g) for g in measures)


def _mappings(gold, cand, spec):
    """Yield (gold_cols, cand_cols, factors): injective mappings honoring the rules in the module docstring."""
    k, m = len(gold[0]), len(cand[0])
    req = set(spec.get("required_cols", range(k))) - set(spec.get("optional_cols", []))
    opt = [j for j in range(k) if j not in req]
    g_num = {j for j in range(k) if _is_numeric_col(gold, j)}
    c_num = {j for j in range(m) if _is_numeric_col(cand, j)}
    c_numstr = {j for j in range(m) if j not in c_num and all(_num(r[j]) is not None for r in cand if r[j] is not None)}
    pct, sgn = set(spec.get("percent_cols", [])), set(spec.get("sign_cols", []))
    for r in range(len(opt) + 1):
        for extra in itertools.combinations(opt, r):
            gcols = sorted(req | set(extra))
            if len(gcols) > m:
                continue
            for ccols in itertools.permutations(range(m), len(gcols)):
                unexplained = c_num - set(ccols)
                unused_optional_measures = [g for g in opt if g in g_num and g not in gcols]
                if unexplained and (unused_optional_measures or   # a count the question defines must be the right one
                                    not _supporting_counts_allowed(gold, cand, gcols, g_num, unexplained, spec)):
                    continue                                      # extra numbers could be alternative answers
                if any((g in g_num) != (c in c_num or (g in g_num and c in c_numstr)) for g, c in zip(gcols, ccols)):
                    continue
                nums = [c for g, c in zip(gcols, ccols) if g in g_num]
                if nums != sorted(nums):                          # measures keep the expected order
                    continue
                opts = []
                for g in gcols:
                    f = [1.0]
                    if g in pct:
                        f.append(0.01)
                    if g in sgn:
                        f += [-x for x in f]
                    opts.append(f)
                for factors in itertools.product(*opts):
                    yield gcols, ccols, factors


def match(gold: list[tuple], cand: list[tuple], spec: dict | None = None) -> tuple[bool, str]:
    spec = spec or {}
    tol = spec.get("tol", 0.005)
    if spec.get("order") == "topn":
        return _match_topn(gold, cand, spec)
    if not gold:
        return (not cand, "ok" if not cand else "expected no rows")
    if not cand:
        return False, "no rows returned"
    if len(gold) != len(cand):
        return False, f"expected {len(gold)} rows, got {len(cand)}"
    for gcols, ccols, factors in _mappings(gold, cand, spec):
        g = [tuple(r[j] for j in gcols) for r in gold]
        c = [tuple(r[j] for j in ccols) for r in cand]
        if spec.get("order") == "strict":
            if all(all(_eq(a, b, tol, f) for a, b, f in zip(gr, cr, factors)) for gr, cr in zip(g, c)):
                return True, "ok"
        elif _rows_equal(g, c, factors, tol):
            return True, "ok"
    return False, "values differ (or extra / missing columns)"


def _match_topn(gold_full: list[tuple], cand: list[tuple], spec: dict) -> tuple[bool, str]:
    """gold_full: every candidate row, best first, metric in `metric_col`. Valid answer: exactly N rows (fewer only if
    fewer exist) with correct values, containing every row strictly better than the N-th and otherwise rows tied with it."""
    n, mc, tol = spec["n"], spec.get("metric_col", 1), spec.get("tol", 0.005)
    if not gold_full:
        return (not cand, "ok" if not cand else "expected no rows")
    n = min(n, len(gold_full))
    if len(cand) != n:
        return False, f"expected {n} rows, got {len(cand)}"
    cutoff = _num(gold_full[n - 1][mc])
    asc = spec.get("ascending", False)
    better = [r for r in gold_full if (_num(r[mc]) < cutoff - tol if asc else _num(r[mc]) > cutoff + tol)]
    tied = [r for r in gold_full if abs(_num(r[mc]) - cutoff) <= tol]
    for gcols, ccols, factors in _mappings(gold_full, cand, spec):
        proj_g = lambda rows: [tuple(r[j] for j in gcols) for r in rows]
        c = [tuple(r[j] for j in ccols) for r in cand]
        remaining = list(c)
        ok = True
        for gr in proj_g(better):                                 # every strictly-better row must be there
            hit = next((i for i, cr in enumerate(remaining) if all(_eq(a, b, tol, f) for a, b, f in zip(gr, cr, factors))), None)
            if hit is None:
                ok = False
                break
            remaining.pop(hit)
        if not ok:
            continue
        pool = proj_g(tied)                                       # the rest: distinct rows from the tied set
        for cr in remaining:
            hit = next((i for i, gr in enumerate(pool) if all(_eq(a, b, tol, f) for a, b, f in zip(gr, cr, factors))), None)
            if hit is None:
                ok = False
                break
            pool.pop(hit)
        if ok:
            return True, "ok"
    return False, "not a valid top-N"
