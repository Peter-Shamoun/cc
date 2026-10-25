"""Majority vote over canonical results, with the margin as the confidence.

The denominator is the number of scheduled attempts, not the number that
managed to execute. A crash is a disagreement with everyone: it lowers the
margin, and it cannot be outvoted by two lucky successes when five were
scheduled.

A strict majority is more than half the scheduled attempts. Tied pluralities
abstain. A strict majority with a small margin escalates (the answer is kept
as a tentative, for a larger model to confirm or replace). A strict majority
with a wide margin is the answer.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from bench.compare import match

from .normalize import canonical_key
from .protocol import ACCEPT_MARGIN, ATTEMPT_COUNT, ESCALATE_MARGIN

BALLOT_STATUSES = ("executed", "refusal", "clarification")


@dataclass
class Attempt:
    """One finished end-to-end attempt. `rows` is what the SQL executed to."""

    attempt_index: int
    status: str
    rows: tuple[tuple, ...] | None = None
    sql: str | None = None
    text: str | None = None
    error: str | None = None
    config_name: str | None = None
    repeat: int | None = None
    extras: dict = field(default_factory=dict)


def decide(
    attempts: list[Attempt],
    *,
    spec: dict | None = None,
    gold_rows: list | tuple | None = None,
    expect_refusal: bool = False,
    expected_attempts: int = ATTEMPT_COUNT,
    accept_margin: float = ACCEPT_MARGIN,
    escalate_margin: float = ESCALATE_MARGIN,
) -> dict:
    """Vote `attempts` and choose accept, escalate, or abstain."""
    if expected_attempts < 1:
        raise ValueError("expected_attempts must be positive")
    if not 0 <= escalate_margin <= accept_margin <= 1:
        raise ValueError("need 0 <= escalate_margin <= accept_margin <= 1")
    spec = spec or {}
    scheduled = max(len(attempts), expected_attempts)
    ballots: list[tuple[Attempt, tuple]] = []
    non_ballots: list[dict] = []
    for attempt in attempts:
        if attempt.status not in BALLOT_STATUSES:
            non_ballots.append({"attempt_index": attempt.attempt_index, "status": attempt.status, "error": attempt.error})
            continue
        try:
            key = _ballot_key(attempt, spec)
        except (TypeError, ValueError) as exc:
            non_ballots.append({
                "attempt_index": attempt.attempt_index, "status": "normalize_error", "error": str(exc)[:300],
            })
            continue
        ballots.append((attempt, key))

    grouped: dict[tuple, list[Attempt]] = defaultdict(list)
    for attempt, key in ballots:
        grouped[key].append(attempt)
    ranked = sorted(grouped.items(), key=lambda item: (-len(item[1]), min(a.attempt_index for a in item[1])))
    winner_attempts: list[Attempt] = ranked[0][1] if ranked else []
    runner_up_votes = len(ranked[1][1]) if len(ranked) > 1 else 0
    winner_votes = len(winner_attempts)
    unique_leader = bool(ranked) and (len(ranked) == 1 or winner_votes > runner_up_votes)
    has_strict_majority = bool(unique_leader and winner_votes * 2 > scheduled)
    margin = ((winner_votes - runner_up_votes) / scheduled) if unique_leader else 0.0

    chosen = min(winner_attempts, key=lambda attempt: attempt.attempt_index) if unique_leader else None
    protocol_complete = len(attempts) == expected_attempts
    action, reason = _action(
        has_strict_majority=has_strict_majority,
        margin=margin,
        protocol_complete=protocol_complete,
        scheduled=scheduled,
        ballots=len(ballots),
        accept_margin=accept_margin,
        escalate_margin=escalate_margin,
        chosen=chosen,
    )
    gold_known = expect_refusal or gold_rows is not None
    attempt_correct = {
        attempt.attempt_index: _is_correct(attempt, gold_rows, spec, expect_refusal)
        for attempt in attempts
    } if gold_known else {}
    if not gold_known:
        voted_correct = None
    elif chosen is None:
        voted_correct = False
    else:
        voted_correct = attempt_correct[chosen.attempt_index]
    any_correct = any(attempt_correct.values()) if gold_known else None

    return {
        "action": action,
        "confidence": round(margin, 4),
        "margin": round(margin, 4),
        "margin_definition": "(winner_votes - runner_up_votes) / scheduled_attempts",
        "winner_votes": winner_votes if unique_leader else 0,
        "runner_up_votes": runner_up_votes if unique_leader else 0,
        "scheduled_attempts": scheduled,
        "attempts_seen": len(attempts),
        "ballots": len(ballots),
        "has_strict_majority": has_strict_majority,
        "protocol_complete": protocol_complete,
        "chosen_status": chosen.status if chosen else None,
        "chosen_attempt_index": chosen.attempt_index if chosen else None,
        "chosen_sql": chosen.sql if chosen else None,
        "chosen_text": (chosen.text or "")[:400] if chosen else None,
        "chosen_row_count": len(chosen.rows) if chosen and chosen.rows is not None else None,
        "chosen_rows_preview": [_json_row(row) for row in (chosen.rows or [])[:20]] if chosen else None,
        "voted_correct": voted_correct,
        "any_attempt_correct": any_correct,
        "cluster_sizes": [len(group) for _key, group in ranked],
        "non_ballots": non_ballots,
        "reason": reason,
    }


def calibrate(question_results: list[dict]) -> dict:
    """Does a higher vote margin come with a higher chance the voted rows are right?

    Bins are the policy cut points: below escalate, between escalate and accept,
    and at or above accept. An item counts when the vote had a unique leader and
    gold (or an expect-refusal label) was available. Ties for the lead are
    reported separately; they have no voted answer.

    `monotonic_in_margin` is whether accuracy does not fall as the bin rises,
    among bins that contain a question. A positive gap can coexist with
    unanimous wrong answers: correlated attempts all miss the same way and the
    margin is 1. Those are counted in `unanimous_wrong` and are the reason a
    high margin is not a proof.
    """
    scored = [
        row for row in question_results
        if row.get("voted_correct") is not None and row.get("chosen_attempt_index") is not None
    ]
    no_leader = [row for row in question_results if row.get("chosen_attempt_index") is None]
    bin_members = {
        "below_escalate": [row for row in scored if row["margin"] < ESCALATE_MARGIN],
        "escalate": [row for row in scored if ESCALATE_MARGIN <= row["margin"] < ACCEPT_MARGIN],
        "accept": [row for row in scored if row["margin"] >= ACCEPT_MARGIN],
    }
    report_bins = []
    for name in ("below_escalate", "escalate", "accept"):
        members = bin_members[name]
        correct = sum(1 for row in members if row["voted_correct"])
        report_bins.append({
            "bin": name,
            "questions": len(members),
            "accuracy": round(correct / len(members), 4) if members else None,
        })
    populated = [bin_row for bin_row in report_bins if bin_row["questions"]]
    monotonic = None
    if len(populated) >= 2:
        monotonic = all(
            populated[index]["accuracy"] <= populated[index + 1]["accuracy"] + 1e-12
            for index in range(len(populated) - 1)
        )
    # With N=5 the weakest unique majority is a 3-2, margin 0.2, which is already
    # the escalate bin. The below-escalate bin stays empty unless a question has
    # no unique leader (those are excluded here) or N is larger. Compare the
    # lowest and highest bins that actually contain questions.
    gap = None
    if len(populated) >= 2:
        gap = round(populated[-1]["accuracy"] - populated[0]["accuracy"], 4)
    actions = [row.get("action") for row in question_results]
    accepted = [row for row in question_results if row.get("action") == "accept" and row.get("voted_correct") is not None]
    return {
        "questions_with_gold": len(scored),
        "questions_with_no_leader": len(no_leader),
        "bins": report_bins,
        "accuracy_gap_high_minus_low": gap,
        "monotonic_in_margin": monotonic,
        "unanimous_wrong": sum(1 for row in scored if row["margin"] >= 1 - 1e-9 and row["voted_correct"] is False),
        "accept_rate": _rate(actions, "accept"),
        "escalate_rate": _rate(actions, "escalate"),
        "abstain_rate": _rate(actions, "abstain"),
        "selective_accuracy_on_accept": (
            round(sum(1 for row in accepted if row["voted_correct"]) / len(accepted), 4) if accepted else None
        ),
        "accuracy_counting_abstain_and_escalate_wrong": _policy_accuracy(question_results),
    }


def _policy_accuracy(question_results: list[dict]) -> float | None:
    """Execution accuracy of the policy: only an accepted correct answer counts."""
    labeled = [row for row in question_results if row.get("voted_correct") is not None]
    if not labeled:
        return None
    correct = sum(1 for row in labeled if row.get("action") == "accept" and row["voted_correct"])
    return round(correct / len(labeled), 4)


def _rate(actions: list[str | None], wanted: str) -> float | None:
    if not actions:
        return None
    return round(sum(action == wanted for action in actions) / len(actions), 4)


def _action(*, has_strict_majority, margin, protocol_complete, scheduled, ballots, accept_margin, escalate_margin, chosen) -> tuple[str, str]:
    if not has_strict_majority or chosen is None:
        return "abstain", f"no strict majority ({ballots} ballots of {scheduled} scheduled)"
    if margin + 1e-12 < escalate_margin:
        return "abstain", f"strict majority but margin {margin:.2f} is below the escalate floor {escalate_margin:.2f}"
    if margin + 1e-12 < accept_margin:
        return "escalate", (
            f"margin {margin:.2f} is a majority but below {accept_margin:.2f}; "
            "cascade to a larger model instead of shipping the tentative answer"
        )
    if not protocol_complete:
        return "escalate", (
            f"margin {margin:.2f} would accept, but {ballots} ballots arrived against "
            f"{scheduled} scheduled attempts"
        )
    return "accept", f"strict majority with margin {margin:.2f}"


def _ballot_key(attempt: Attempt, spec: dict) -> tuple:
    if attempt.status == "refusal":
        return ("refusal",)
    if attempt.status == "clarification":
        return ("clarification",)
    if attempt.rows is None:
        raise ValueError("executed attempt has no rows")
    return canonical_key(attempt.rows, spec)


def _is_correct(attempt: Attempt, gold_rows, spec: dict, expect_refusal: bool) -> bool:
    if expect_refusal:
        return attempt.status == "refusal"
    if attempt.status != "executed" or attempt.rows is None:
        return False
    ok, _why = match(list(gold_rows), [tuple(row) for row in attempt.rows], spec)
    return ok


def _json_row(row: tuple) -> list:
    return [None if cell is None else cell if isinstance(cell, (str, int, float, bool)) else str(cell) for cell in row]
