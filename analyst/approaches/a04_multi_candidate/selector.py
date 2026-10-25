"""Execution agreement, sanity checks, and the decision to call a pairwise selector.

A candidate votes only when its SQL executed and the result is not an empty set or a rate
outside 0..100. Voters are grouped by the values they returned (column names and row order
do not matter). One agreeing group is the answer. A strict majority is the answer. Anything
else is a disagreement, and the caller makes one pairwise comparison. Soft warnings (the
grain and filter traps from the semantic layer) are shown to that comparison and break ties
inside a group; they do not throw a result out.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .prompts import STRATEGIES

_RATE_NAME = re.compile(r"rate|pct|percent|share", re.IGNORECASE)
_CLAIMS = re.compile(r"sem\.claim(s|_lines)\b", re.IGNORECASE)

# Result-level failures. A number that fails one of these does not get to vote.
HARD_WARNING_CODES = ("empty_result", "rate_out_of_range")


@dataclass
class Candidate:
    strategy: str
    raw: str = ""
    sql: str | None = None
    notes: str = ""
    refused: bool = False
    refusal_reason: str = ""
    columns: tuple = ()
    rows: tuple = ()
    error: str | None = None
    warnings: tuple = ()


@dataclass
class Selection:
    """The policy decision. `rows` is None unless kind is 'answer'."""

    kind: str
    reason: str
    sql: str | None = None
    rows: tuple | None = None
    columns: tuple = ()
    strategy: str | None = None
    selector_used: bool = False
    selector_reason: str = ""
    dropped_strategies: tuple = ()
    voter_strategies: tuple = ()
    candidates: list = field(default_factory=list)


def is_hard_warning(warning: str) -> bool:
    return warning.startswith(tuple(code + ":" for code in HARD_WARNING_CODES))


def sanity_warnings(sql: str, columns, rows) -> tuple[str, ...]:
    """Warnings on one executed result. Hard codes change the vote; the others are annotations."""
    warnings = []
    if not rows:
        warnings.append("empty_result: the query returned no rows")
    for index, name in enumerate(columns):
        values = [row[index] for row in rows]
        if rows and all(value is None for value in values):
            warnings.append(f"all_null_column: column {name} is NULL in every row")
        if _RATE_NAME.search(str(name)) and any(_out_of_rate_range(value) for value in values):
            warnings.append(f"rate_out_of_range: column {name} has a value outside 0..100")
    lowered = sql.lower()
    if "remit_claims" in lowered and "is_current_adjudication" not in lowered:
        warnings.append("missing_current_adjudication: sem.remit_claims used without is_current_adjudication")
    if _CLAIMS.search(sql) and "is_latest_submission" not in lowered:
        warnings.append("missing_latest_submission: sem.claims or sem.claim_lines used without is_latest_submission")
    if rows and columns and len(columns) > 1 and _first_column_repeats(rows):
        warnings.append(f"repeated_key: the first column ({columns[0]}) repeats across rows")
    return tuple(warnings)


def fingerprint(columns, rows) -> tuple:
    """Value identity of a result. Names and order are ignored; width is not.

    Integral floats match integers, so a count typed as 2 and a count typed as 2.0 agree.
    Column order is part of the identity: (paid, denied) is not the same result as
    (denied, paid). The bench matcher is more generous than this; a cosmetic split
    falls through to the selector instead of being forced into a false agreement.
    """
    width = len(tuple(columns))
    normalized = tuple(sorted(tuple(_norm_cell(cell) for cell in row) for row in rows))
    return (width, normalized)


def select_candidates(candidates: list[Candidate], choose_pair) -> Selection:
    """`choose_pair(left, right) -> (choice, reason)` with choice in {'a', 'b', 'abstain'}.

    Called at most once. `a` is the representative with the earlier strategy, not a better one.
    On a three-way split the cluster with the most soft warnings is left out of that one call
    (earlier strategy wins a tie). CHASE runs a full tournament; the screen-2 design for this
    harness budgets one selector call, so the third result is reported as dropped instead.
    """
    refusals = [c for c in candidates if c.refused]
    if len(refusals) >= 2:
        reasons = "; ".join(c.refusal_reason for c in refusals if c.refusal_reason)
        return Selection(kind="refuse", reason=f"refuse: {reasons}", candidates=candidates)

    executed = [c for c in candidates if c.sql and c.error is None and not c.refused]
    if not executed:
        return Selection(kind="abstain", reason="abstain: no candidate executed", candidates=candidates)

    voters = [c for c in executed if not _is_hard_candidate(c)]
    if not voters:
        return Selection(kind="abstain", reason="abstain: sanity checks rejected every executed result",
                         candidates=candidates)

    clusters = _clusters(voters)
    dropped_hard = len(executed) - len(voters)
    if len(clusters) == 1:
        chosen = clusters[0]
        if dropped_hard:
            reason = (f"sanity: {len(voters)} candidate(s) agree after sanity checks dropped {dropped_hard} "
                      f"executed candidate(s)")
        else:
            reason = f"agreement: {len(voters)} of {len(executed)} executed candidates returned the same result"
        return _answer(chosen, reason, candidates, voter_strategies=tuple(c.strategy for c in voters))

    majority = [cluster for cluster in clusters if len(cluster) > len(executed) / 2]
    if majority:
        chosen = majority[0]
        reason = f"majority: {len(chosen)} of {len(executed)} executed candidates agree"
        return _answer(chosen, reason, candidates, voter_strategies=tuple(c.strategy for c in voters))

    ranked = sorted(clusters, key=_cluster_rank)
    left_cluster, right_cluster = ranked[0], ranked[1]
    dropped = tuple(representative(cluster).strategy for cluster in ranked[2:])
    left, right = representative(left_cluster), representative(right_cluster)
    if STRATEGIES.index(right.strategy) < STRATEGIES.index(left.strategy):
        left, right = right, left
        left_cluster, right_cluster = right_cluster, left_cluster
    choice, selector_reason = choose_pair(left, right)
    if choice not in ("a", "b", "abstain"):
        choice, selector_reason = "abstain", selector_reason or "selector returned an unusable choice"
    if choice == "abstain":
        return Selection(kind="abstain", reason=f"abstain: selector declined ({selector_reason})",
                         selector_used=True, selector_reason=selector_reason, dropped_strategies=dropped,
                         voter_strategies=tuple(c.strategy for c in voters), candidates=candidates)
    chosen = left_cluster if choice == "a" else right_cluster
    picked = representative(chosen)
    return _answer(chosen, f"pairwise: selector chose {picked.strategy} ({selector_reason})", candidates,
                   selector_used=True, selector_reason=selector_reason, dropped_strategies=dropped,
                   voter_strategies=tuple(c.strategy for c in voters))


def representative(cluster: list[Candidate]) -> Candidate:
    """The SQL we would submit for a cluster: fewest soft warnings, then the earliest strategy."""
    return min(cluster, key=lambda c: (_soft_count(c), STRATEGIES.index(c.strategy)))


def _answer(cluster, reason, candidates, *, selector_used=False, selector_reason="", dropped_strategies=(),
            voter_strategies=()) -> Selection:
    picked = representative(cluster)
    return Selection(kind="answer", reason=reason, sql=picked.sql, rows=picked.rows, columns=picked.columns,
                     strategy=picked.strategy, selector_used=selector_used, selector_reason=selector_reason,
                     dropped_strategies=tuple(dropped_strategies), voter_strategies=tuple(voter_strategies),
                     candidates=candidates)


def _clusters(voters: list[Candidate]) -> list[list[Candidate]]:
    groups: dict[tuple, list[Candidate]] = {}
    for candidate in voters:
        groups.setdefault(fingerprint(candidate.columns, candidate.rows), []).append(candidate)
    return list(groups.values())


def _cluster_rank(cluster: list[Candidate]):
    rep = representative(cluster)
    return (_soft_count(rep), STRATEGIES.index(rep.strategy))


def _is_hard_candidate(candidate: Candidate) -> bool:
    return any(is_hard_warning(warning) for warning in candidate.warnings)


def _soft_count(candidate: Candidate) -> int:
    return sum(not is_hard_warning(warning) for warning in candidate.warnings)


def _first_column_repeats(rows) -> bool:
    try:
        return len({row[0] for row in rows}) < len(rows)
    except TypeError:
        return False


def _out_of_rate_range(value) -> bool:
    number = _as_float(value)
    if number is None:
        return False
    return number < 0 or number > 100


def _as_float(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float, Decimal)):
        number = float(value)
        if math.isnan(number):
            return None
        return number
    return None


def _norm_cell(value):
    if value is None:
        return ("none",)
    if isinstance(value, bool):
        return ("bool", value)
    if isinstance(value, int):
        return ("int", value)
    if isinstance(value, Decimal):
        number = float(value)
        if math.isnan(number):
            return ("nan",)
        return _norm_number(number, value == value.to_integral_value())
    if isinstance(value, float):
        if math.isnan(value):
            return ("nan",)
        return _norm_number(value, math.isfinite(value) and value.is_integer())
    if isinstance(value, datetime):
        return ("datetime", value.isoformat())
    if isinstance(value, date):
        return ("date", value.isoformat())
    if isinstance(value, bytes):
        return ("bytes", value.decode("utf-8", "replace"))
    return ("text", str(value).strip().lower())


def _norm_number(number: float, integral: bool):
    if integral and math.isfinite(number) and abs(number) < 2**53:
        return ("int", int(number))
    return ("num", round(number, 6))
