"""Deterministic answer matching. An agent's result is correct when it contains the expected result:

- Column-tolerant: the expected columns must map one-to-one onto candidate columns (any order, any names);
  extra candidate columns are fine.
- Row-exact: same number of rows, same multiset of rows over the mapped columns. Order is checked only
  when the question asks for a ranking (`order: strict`), and `order: topn` accepts any valid top-N under
  ties at the cut-off.
- Numbers compare with an absolute tolerance per question; `allow_percent` also accepts rates given as
  percentages (0.25 vs 25.0); `sign_insensitive` accepts a sign flip (credits reported as positive amounts).

Anything this rejects can still be appealed to the LLM judge, which sees both results.
"""
from __future__ import annotations

import itertools
import math
from collections import Counter
from datetime import date, datetime
from decimal import Decimal


def _num(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float, Decimal)):
        f = float(v)
        return None if math.isnan(f) else f
    return None


def _cell_eq(g, c, tol: float, pct: bool, sign: bool) -> bool:
    gn, cn = _num(g), _num(c)
    if gn is not None and cn is not None:
        opts = [cn] + ([cn / 100] if pct else []) + ([-cn] if sign else []) + ([-cn / 100] if pct and sign else [])
        return any(abs(gn - x) <= tol + 1e-12 for x in opts)
    if g is None or c is None:
        return g is None and c is None
    if isinstance(g, (date, datetime)) or isinstance(c, (date, datetime)):
        return str(g)[:10] == str(c)[:10]
    if isinstance(g, (list, tuple)) and isinstance(c, (list, tuple)):
        return sorted(map(str, g)) == sorted(map(str, c))
    return str(g).strip().lower() == str(c).strip().lower()


def _key(v, tol):
    n = _num(v)
    if n is not None:
        return ("n", round(n / max(tol, 1e-9)))
    if isinstance(v, (date, datetime)):
        return ("s", str(v)[:10])
    if isinstance(v, (list, tuple)):
        return ("l", tuple(sorted(map(str, v))))
    return ("s", "" if v is None else str(v).strip().lower())


def _rows_match(gold, cand, mapping, spec) -> bool:
    tol, pct, sign = spec.get("tol", 0.005), spec.get("allow_percent", False), spec.get("sign_insensitive", False)
    proj = [tuple(r[j] for j in mapping) for r in cand]
    if spec.get("order") == "strict":
        return all(all(_cell_eq(g, c, tol, pct, sign) for g, c in zip(gr, cr)) for gr, cr in zip(gold, proj))
    # order-insensitive: greedy matching over a sort, then a full check (handles tolerance at bucket edges)
    remaining = list(proj)
    for gr in gold:
        hit = next((i for i, cr in enumerate(remaining) if all(_cell_eq(g, c, tol, pct, sign) for g, c in zip(gr, cr))), None)
        if hit is None:
            return False
        remaining.pop(hit)
    return True


def match(gold: list[tuple], cand: list[tuple], spec: dict | None = None) -> tuple[bool, str]:
    spec = spec or {}
    if spec.get("order") == "topn":
        return _match_topn(gold, cand, spec)
    if not gold:
        return (not cand, "expected no rows" if cand else "ok")
    if not cand:
        return False, "no rows returned"
    if len(gold) == 1 and len(gold[0]) == 1 and len(cand) == 1:          # scalar: any cell may hold it
        tol, pct, sign = spec.get("tol", 0.005), spec.get("allow_percent", False), spec.get("sign_insensitive", False)
        ok = any(_cell_eq(gold[0][0], c, tol, pct, sign) for c in cand[0])
        return ok, "ok" if ok else f"expected {gold[0][0]}, got {cand[0]}"
    if len(gold) != len(cand):
        return False, f"expected {len(gold)} rows, got {len(cand)}"
    k, m = len(gold[0]), len(cand[0])
    if m < k:
        return False, f"expected at least {k} columns, got {m}"
    tol = spec.get("tol", 0.005)
    # prune: a gold column can only map to candidate columns with a compatible value multiset
    sig = lambda rows, j: Counter(_key(r[j], tol) for r in rows)
    options = []
    for i in range(k):
        gsig = sig(gold, i)
        opts = [j for j in range(m) if sig(cand, j) == gsig] or list(range(m))
        options.append(opts)
    for mapping in itertools.product(*options):
        if len(set(mapping)) == k and _rows_match(gold, cand, mapping, spec):
            return True, "ok"
    return False, "values differ"


def _match_topn(gold_full: list[tuple], cand: list[tuple], spec: dict) -> tuple[bool, str]:
    """gold_full: every candidate row, sorted best-first, with the ranking metric in column `metric_col`.
    A valid answer has exactly N rows (fewer only if there are fewer), all with correct values, containing every
    row strictly better than the N-th metric and otherwise any rows tied with it."""
    n, mc, tol = spec["n"], spec.get("metric_col", 1), spec.get("tol", 0.005)
    if not gold_full:
        return (not cand, "ok" if not cand else "expected no rows")
    n = min(n, len(gold_full))
    if len(cand) != n:
        return False, f"expected {n} rows, got {len(cand)}"
    cutoff = _num(gold_full[n - 1][mc])
    better = lambda r: _num(r[mc]) is not None and (_num(r[mc]) > cutoff + tol if not spec.get("ascending") else _num(r[mc]) < cutoff - tol)
    tied = lambda r: _num(r[mc]) is not None and abs(_num(r[mc]) - cutoff) <= tol
    must = [r for r in gold_full if better(r)]
    may = [r for r in gold_full if tied(r)]
    for size in (len(must),):
        for extra in itertools.combinations(may, n - size):
            ok, why = match(must + list(extra), cand, {**spec, "order": None})
            if ok:
                return True, "ok"
            if len(may) > 12:                                               # bound the search on huge ties
                break
    return False, "not a valid top-N"
