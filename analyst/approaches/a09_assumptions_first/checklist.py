"""Interpretation checklist for lab-billing questions over sem.*.

Five axes, the ones that moved benchmark answers when a question left them open:
population, which claims count, date basis (service, received, or paid), units,
and ties. `analyze` picks the reading a revenue-cycle analyst would lock.
Materiality is structural: a fork is ask-worthy when this schema makes the two
readings different numbers and the question did not name one. The checklist does
not run SQL to find out. That judgment is the trained artifact — do not edit it
against a validation split.

The canonical menu for prompts is `render_checklist`. Selection rules in that
text are the policy this module implements.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

# A denial the glossary already defines. Stated when the question says "denial"
# and does not itself say status 4 or the signal rule. $0 paid is not this.
DENIAL_ASSUMPTION = (
    "Assuming a denial is is_denied (a recorded claim status 4 or the signal rule), not every $0 payment."
)

SELECTION_RULES = """\
Population — who is in the set. The extract starts mid-stream: claims billed before the first file exist only as 835s.
- "every submission" -> all_submission_rows.
- "no current adjudication" or a reversal with no current row -> remit_events. Do not treat that as the current set.
- "latest submission" (and the question is not about the primary payer) -> latest_submission_any_payer. That row may be the secondary payer.
- primary, and the question is about the submission or days-to-first-remit rather than a denial or a payment -> primary_submission (payer_responsibility = 'P').
- primary, and the question is a denial, a payment, or an adjudication -> primary_current. State it when the question did not also say current.
- the question names the current adjudication -> current_adjudication.
- submitted, billed, resubmitted, or voided, and not a payment or a denial -> submitted_837.
- paid, collected, denial, or adjudication, with no more specific phrase -> current_adjudication, including remit-only claims. State it.
- a bare "how many claims", with no verb that picks 837 vs 835 -> submitted_837, and a product UI asks. Remit-only claims change the count.

Which claims count — what one row means.
- "service line" -> service_line.
- "835 payments", checks, or EFTs -> payment (sem.payments, already one row per re-delivered file).
- "claim+payer", "once per payer", "with each payer", or "per payer" -> claim_per_payer.
- "distinct claims", "patient control number", or "each claim once" -> distinct_claim_key.
- the question names the current adjudication -> claim_per_payer.
- a denial rate or "denied" -> claim_per_payer. State it when the question did not say so.
- "how many claims" -> distinct_claim_key. State it.
- a paid or collected amount -> once per claim and payer. State it.
- a billed or submitted count -> distinct_claim_key. State it.

Date basis — service, received, or paid. Open only when the question names a period, a date, or a span of days.
- "days from" one event until another -> endpoints_named. Keep both ends.
- "payment month" or "payment date" -> paid (payment_date / payment_month, the 835 effective date).
- "service month", "service date", or DOS -> service.
- "submission date" or "payer received" -> received. Received means submission_date, or payer_received_date when the payer's receipt is what was asked. file_received_at is the ingest timestamp, not a billing date.
- otherwise a paid, denial, or remit measure -> paid, and a product UI asks, because service month is a different answer.
- otherwise -> submission date, and a product UI asks.

Units — open only when the question says "unit" or "units".
- "billed units" -> units_billed on the latest 837 submission.
- "paid units" -> units_paid on the current adjudication.
- otherwise units_billed, and a product UI asks. Billed and paid units are different numbers.

Ties — open on top-N, which-N, highest, lowest, most, or least.
- the question states the tie-break -> stated_in_question.
- otherwise the measure, then the label ascending, cut at N. State it. Do not ask. A tie at the cutoff is reported with the answer.

Denial definition — when the question says denial and does not already say status 4 or the signal rule, state: """ + DENIAL_ASSUMPTION + """

Voids — a billed total includes a voided claim only when the question says to include voids. The menu says so. SQL is not gated on it.

Ask in a product UI only for a fork marked ask-if-unpinned that the question did not pin: the bare claim population, an unstated date basis, and unqualified units. Every other fork is stated with the number. The benchmark config never asks.
"""


@dataclass(frozen=True)
class Reading:
    id: str
    label: str


@dataclass(frozen=True)
class Decision:
    axis_id: str
    title: str
    readings: tuple[Reading, ...]
    chosen_id: str
    pinned: bool
    ask_if_unpinned: bool
    statement: str
    assumptions: tuple[str, ...]
    ask_prompt: str


POPULATION_READINGS = (
    Reading("submitted_837", "Claims with an 837 in the extract. Remit-only claims are out."),
    Reading("current_adjudication", "Current 835 adjudications (filter is_current_adjudication), one per claim and payer, including claims with no 837."),
    Reading("all_submission_rows", "Every 837 row. A resubmission or a secondary bill counts again."),
    Reading("latest_submission_any_payer", "is_latest_submission. That row may be the secondary payer."),
    Reading("remit_events", "Every 835 event, including reversals and superseded payments."),
    Reading("primary_submission", "The primary 837 only (payer_responsibility = 'P')."),
    Reading("primary_current", "The primary payer's current adjudication, joined from payer_responsibility = 'P'."),
)
WHICH_READINGS = (
    Reading("distinct_claim_key", "One patient control number (claim_key)."),
    Reading("claim_per_payer", "One current adjudication per claim and payer."),
    Reading("every_submission_row", "One row per 837 submission."),
    Reading("every_remit_event", "One row per 835 event."),
    Reading("service_line", "One service line."),
    Reading("payment", "One 835 payment (check, EFT, or zero-pay notice)."),
)
DATE_READINGS = (
    Reading("service", "Date of service (service_date / service_month)."),
    Reading("received", "Submission date, or payer_received_date when the payer's receipt is named."),
    Reading("paid", "Paid date (payment_date / payment_month, the 835 effective date)."),
    Reading("endpoints_named", "The question names both ends of the span."),
)
UNIT_READINGS = (
    Reading("billed_units", "Units billed on the 837 (units_billed), latest submission."),
    Reading("paid_units", "Units the payer paid (units_paid) on the current adjudication."),
)
TIE_READINGS = (
    Reading("measure_then_label", "Measure, then the label ascending, cut at N."),
    Reading("measure_only", "Measure only. Ties at the cutoff are unstable."),
    Reading("keep_all_ties", "Keep every row tied at the cutoff, even past N."),
    Reading("stated_in_question", "The tie-break the question already states."),
)

_ASSUMPTION = {
    "submitted_837": (
        "Assuming the submitted population: claims on an 837 in this extract, excluding claims that "
        "appear only on an 835 because the files start mid-stream."
    ),
    "current_adjudication": (
        "Assuming current adjudications: one row per claim and payer (is_current_adjudication), "
        "including claims that have no 837 in the extract."
    ),
    "primary_current": (
        "Assuming the primary payer's current adjudication (payer_responsibility = 'P' joined to "
        "is_current_adjudication), not the latest submission's payer."
    ),
    "distinct_claim_key": "Assuming a claim is one patient control number (claim_key), not one 837 submission row.",
    "claim_per_payer": (
        "Assuming each claim is counted once per payer on its current adjudication, not once per 835 event."
    ),
    "service_line": "Assuming one service line, not one claim.",
    "paid": (
        "Assuming the paid date (payment_date / payment_month, the 835 effective date), not the date of "
        "service or the submission date."
    ),
    "received": (
        "Assuming the received date is the 837 submission date (submission_date), not the file ingest "
        "timestamp and not the date of service."
    ),
    "service": "Assuming the date of service (service_date / service_month).",
    "billed_units": "Assuming billed units (units_billed on the latest 837 submission), not the units the payer paid.",
    "paid_units": "Assuming paid units (units_paid on the current adjudication), not the units we billed.",
    "measure_then_label": (
        "Assuming ties break by the measure, then the label ascending, and the list stops at the requested count."
    ),
}

_TIME = re.compile(
    r"\b(month|quarter|week|year|date|between|ytd|yesterday|today|days?)\b|\b20\d{2}\b|"
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b"
)


def _decision(axis_id: str, title: str, readings: tuple[Reading, ...], chosen_id: str, *,
              pinned: bool, ask: bool, assumption: str | None, ask_prompt: str) -> Decision:
    if chosen_id not in {reading.id for reading in readings}:
        raise RuntimeError(f"{axis_id} locked {chosen_id}, which is not on its menu")
    statement = assumption or f"The question pins {chosen_id}."
    return Decision(
        axis_id=axis_id, title=title, readings=readings, chosen_id=chosen_id, pinned=pinned,
        ask_if_unpinned=ask and not pinned, statement=statement,
        assumptions=() if assumption is None else (assumption,), ask_prompt=ask_prompt)


def _says_current(q: str) -> bool:
    if re.search(r"no current adjudication|not current adjudication|without a current adjudication", q):
        return False
    return "current adjudication" in q or bool(re.search(r"\bcurrent\b", q) and re.search(r"adjudications?", q))


def _claim_population(q: str) -> bool:
    """False for questions whose subject is a payment instrument, a file, a payer list, or a PLB."""
    if re.search(r"835 payments|distinct payers|edi files|provider-level|provider level|\bplb\b", q):
        return False
    return bool(re.search(
        r"\b(claims?|billed|denials?|denied|adjudications?|adjudicated|submitted|submissions?)\b|\bresubmit|\bvoid(?:ed)?\b", q)
        or re.search(r"\b(paid|payment|collected)\b", q))


def _population(q: str) -> Decision | None:
    if not _claim_population(q):
        return None
    ask = "population: should the set be only claims we submitted on an 837, or also claims that appear only on an 835 because the extract starts mid-stream?"
    def lock(chosen: str, *, pinned: bool, ask_this: bool = False, assume: bool = False) -> Decision:
        return _decision(
            "population", "Population", POPULATION_READINGS, chosen, pinned=pinned, ask=ask_this,
            assumption=_ASSUMPTION[chosen] if assume else None, ask_prompt=ask)
    if re.search(r"every submission|all submissions|each submission row", q):
        return lock("all_submission_rows", pinned=True)
    if re.search(r"every 835 event|include reversals|no current adjudication", q):
        return lock("remit_events", pinned=True)
    if re.search(r"latest submission", q) and not re.search(r"\bprimary\b", q):
        return lock("latest_submission_any_payer", pinned=True)
    about_money = re.search(r"\b(denials?|denied|paid|payment|collected|adjudications?|adjudicated)\b", q)
    if re.search(r"\bprimary\b", q) and re.search(r"\b(submission|submitted)\b", q) and not about_money:
        return lock("primary_submission", pinned=True)
    if re.search(r"\bprimary\b", q) and about_money:
        return lock("primary_current", pinned=_says_current(q), assume=not _says_current(q))
    if _says_current(q):
        return lock("current_adjudication", pinned=True)
    about_bill = re.search(r"\b(submitted|submission|billed)\b|\bresubmit|\bvoid(?:ed)?\b", q)
    if about_bill and not about_money:
        return lock("submitted_837", pinned=True)
    if about_money:
        return lock("current_adjudication", pinned=False, assume=True)
    return lock("submitted_837", pinned=False, ask_this=True, assume=True)


def _which_claims(q: str) -> Decision | None:
    if re.search(r"provider-level|provider level|\bplb\b|\binterest\b", q) and not re.search(r"\bclaims?\b", q):
        return None
    if re.search(r"distinct payers|edi files", q):
        return None
    if not re.search(r"\b(claims?|lines?|denials?|denied|payments?|checks|efts|adjudications?|paid|billed|submitted|resubmit)\b", q):
        return None
    ask = "which_claims: should one row be a claim, a claim and a payer, or a service line?"
    def lock(chosen: str, *, pinned: bool, ask_this: bool = False) -> Decision:
        return _decision(
            "which_claims", "Which claims count", WHICH_READINGS, chosen, pinned=pinned, ask=ask_this,
            assumption=None if pinned else _ASSUMPTION[chosen], ask_prompt=ask)
    if re.search(r"\bservice lines?\b", q):
        return lock("service_line", pinned=True)
    if re.search(r"835 payments|\bchecks\b|\befts?\b|zero-pay notices", q):
        return lock("payment", pinned=True)
    if re.search(r"claim\s*\+\s*payer|once per payer|with each payer|per payer", q):
        return lock("claim_per_payer", pinned=True)
    if re.search(r"distinct claims|patient control|each claim once", q):
        return lock("distinct_claim_key", pinned=True)
    if _says_current(q):
        return lock("claim_per_payer", pinned=True)
    if re.search(r"\bdenial rate\b|\bdenied\b", q):
        return lock("claim_per_payer", pinned=False)
    if re.search(r"how many\b.*\bclaims?\b", q):
        return lock("distinct_claim_key", pinned=False)
    if re.search(r"\b(paid|payment|collected)\b", q):
        return lock("claim_per_payer", pinned=False)
    if re.search(r"\b(billed|submitted|submission|resubmit)\b", q):
        return lock("distinct_claim_key", pinned=False)
    both = bool(re.search(r"\blines?\b", q) and re.search(r"\bclaims?\b", q))
    return lock("distinct_claim_key", pinned=False, ask_this=both)


def _date_basis(q: str) -> Decision | None:
    ask = ("date_basis: the period is not tied to a date. Should it be the paid date (payment_month), "
           "the date of service, or the date the claim was submitted?")
    def lock(chosen: str, *, pinned: bool, ask_this: bool = False) -> Decision:
        return _decision(
            "date_basis", "Date basis", DATE_READINGS, chosen, pinned=pinned, ask=ask_this,
            assumption=None if pinned or chosen == "endpoints_named" else _ASSUMPTION[chosen], ask_prompt=ask)
    if re.search(r"\bdays?\b", q) and re.search(r"\bfrom\b", q) and re.search(r"\buntil\b|\bto the first\b", q):
        return lock("endpoints_named", pinned=True)
    if not _TIME.search(q):
        return None
    if re.search(r"payment date|payment month|paid date|date paid", q):
        return lock("paid", pinned=True)
    if re.search(r"service date|service month|date of service|\bdos\b", q):
        return lock("service", pinned=True)
    if re.search(r"payer received|date received|received date|submission date|submission month", q):
        return lock("received", pinned=True)
    if re.search(r"\b(paid|payment|collected|denials?|denied|remit)\b", q):
        return lock("paid", pinned=False, ask_this=True)
    return lock("received", pinned=False, ask_this=True)


def _units(q: str) -> Decision | None:
    if not re.search(r"\bunits?\b", q):
        return None
    ask = "units: should that be the units we billed (units_billed) or the units the payer paid (units_paid)?"
    def lock(chosen: str, *, pinned: bool, ask_this: bool = False) -> Decision:
        return _decision(
            "units", "Units", UNIT_READINGS, chosen, pinned=pinned, ask=ask_this,
            assumption=None if pinned else _ASSUMPTION[chosen], ask_prompt=ask)
    if re.search(r"billed units|units billed|units we billed", q):
        return lock("billed_units", pinned=True)
    if re.search(r"paid units|units paid|units the payer paid|units were paid", q):
        return lock("paid_units", pinned=True)
    return lock("billed_units", pinned=False, ask_this=True)


def _ties(q: str) -> Decision | None:
    # "at least N" is a threshold, not a ranking. "the least" still is.
    if not re.search(r"\btop\s+\d+\b|\bwhich\s+\d+\b|\bhighest\b|\blowest\b|\bmost\b|(?<!at )\bleast\b", q):
        return None
    ask = "ties: how should a tie at the cutoff be broken?"
    if re.search(r"ties broken|tie-break|tie break|then by", q):
        return _decision("ties", "Ties", TIE_READINGS, "stated_in_question", pinned=True, ask=False,
                         assumption=None, ask_prompt=ask)
    return _decision("ties", "Ties", TIE_READINGS, "measure_then_label", pinned=False, ask=False,
                     assumption=_ASSUMPTION["measure_then_label"], ask_prompt=ask)


def _with_denial_assumption(q: str, decisions: list[Decision]) -> list[Decision]:
    if not decisions or not re.search(r"\bdenial|\bdenied\b", q):
        return decisions
    if re.search(r"status 4|signal rule|signal v3|is_denied", q):
        return decisions
    host_id = "which_claims" if any(d.axis_id == "which_claims" for d in decisions) else decisions[0].axis_id
    updated = []
    for decision in decisions:
        if decision.axis_id == host_id and DENIAL_ASSUMPTION not in decision.assumptions:
            updated.append(replace(decision, assumptions=decision.assumptions + (DENIAL_ASSUMPTION,)))
        else:
            updated.append(decision)
    return updated


def analyze(question: str) -> list[Decision]:
    """Open axes for this question, each already locked to the standard reading."""
    q = " ".join(question.lower().split())
    decisions = [decision for builder in (_population, _which_claims, _date_basis, _units, _ties)
                 if (decision := builder(q)) is not None]
    return _with_denial_assumption(q, decisions)


def assumption_sentences(decisions: list[Decision]) -> list[str]:
    """Sentences the answer must contain. Pinned axes contribute nothing."""
    return [sentence for decision in decisions for sentence in decision.assumptions]


def asking_axes(decisions: list[Decision]) -> list[Decision]:
    return [decision for decision in decisions if decision.ask_if_unpinned and not decision.pinned]


def render_checklist() -> str:
    """The menu a prompt shows. Same readings `analyze` locks."""
    lines = ["Axes and the readings that have to be listed before any SQL:", ""]
    menus = (
        ("population", "Population", POPULATION_READINGS),
        ("which_claims", "Which claims count", WHICH_READINGS),
        ("date_basis", "Date basis (service, received, or paid)", DATE_READINGS),
        ("units", "Units", UNIT_READINGS),
        ("ties", "Ties", TIE_READINGS),
    )
    for axis_id, title, readings in menus:
        lines.append(f"{title} ({axis_id})")
        lines.extend(f"- {reading.id}: {reading.label}" for reading in readings)
        lines.append("")
    lines.append(SELECTION_RULES.rstrip())
    return "\n".join(lines)


def sql_violations(sql: str, decisions: list[Decision], question: str) -> list[str]:
    """Ways `sql` disagrees with the locked readings. String checks, not a compiler.

    A query can still be wrong after these pass. They exist to stop the traps that
    already showed up as benchmark defects: current-adjudication filter dropped,
    latest submission attributed to the secondary payer, service month used for a
    paid question, billed units swapped with paid units, $0 treated as a denial.
    """
    low = " ".join(sql.lower().split())
    q = " ".join(question.lower().split())
    chosen = {decision.axis_id: decision.chosen_id for decision in decisions}
    violations: list[str] = []
    population = chosen.get("population")
    remit = r"sem\.(remit_claims|remit_lines|adjustments|remarks)\b"
    billing = r"sem\.(claims|claim_lines)\b"
    if population in {"current_adjudication", "primary_current"}:
        if not re.search(remit, low):
            violations.append(
                "the locked population is current adjudications; read sem.remit_claims "
                "(or remit lines, adjustments, or remarks), not the 837 alone")
        elif "is_current_adjudication" not in low:
            violations.append(
                "the locked population is current adjudications; filter is_current_adjudication "
                "so reversals and superseded payments are not counted again")
    if population == "primary_current" and "payer_responsibility" not in low:
        violations.append(
            "the primary payer is payer_responsibility = 'P' on sem.claims; "
            "payer_sequence on the 835 is unknown for denials and reversals")
    if population == "primary_submission" and "payer_responsibility" not in low:
        violations.append("the locked population is the primary 837; filter payer_responsibility = 'P'")
    if population == "submitted_837":
        if not re.search(billing, low):
            violations.append("the locked population is claims we submitted; read sem.claims or sem.claim_lines")
        elif "is_latest_submission" not in low and not re.search(r"distinct\s+(?:\w+\.)?claim_key", low):
            violations.append("resubmissions are extra rows; use is_latest_submission or count distinct claim_key")
    if population == "latest_submission_any_payer" and re.search(billing, low) and "is_latest_submission" not in low:
        violations.append("the locked population is the latest submission; filter is_latest_submission")
    if population == "all_submission_rows" and re.search(r"distinct\s+(?:\w+\.)?claim_key|is_latest_submission", low):
        violations.append("the lock is every submission row; do not collapse to one row per claim")
    if population == "remit_events" and not re.search(r"sem\.(remit_claims|remit_lines)\b", low):
        violations.append("the lock is 835 events; read sem.remit_claims or sem.remit_lines")
    if (re.search(r"sem\.claims\b", low) and "is_latest_submission" in low and "payer_name" in low
            and "payer_responsibility" not in low):
        violations.append(
            "is_latest_submission can be the secondary payer's row; do not attribute the claim to that payer")
    if chosen.get("which_claims") == "distinct_claim_key" and re.search(r"\bcount\s*\(", low):
        if ("is_latest_submission" not in low
                and not re.search(r"count\s*\(\s*distinct\s+(?:\w+\.)?claim_key\s*\)", low)):
            violations.append("count distinct claim_key, or one row per claim via is_latest_submission")
    if chosen.get("which_claims") == "payment" and "sem.payments" not in low:
        violations.append("835 payments are rows in sem.payments; a re-delivered file is already one row")
    date = chosen.get("date_basis")
    if date == "paid" and not re.search(r"payment_month|payment_date", low):
        violations.append("the locked date is the paid date; use payment_date or payment_month")
    if date == "service" and not re.search(r"service_month|service_date", low):
        violations.append("the locked date is the date of service; use service_date or service_month")
    if date == "received" and not re.search(r"submission_month|submission_date|payer_received_date", low):
        violations.append(
            "the locked received date is submission_date (or payer_received_date when the payer's receipt "
            "was asked); file_received_at is the ingest timestamp")
    if date == "endpoints_named" and "days_submission_to_first_remit" not in low and not (
            "submission_date" in low and re.search(r"payment_date|first_remit|first_payment", low)):
        violations.append("the question names both endpoints; keep the submission date and the first remit date")
    units = chosen.get("units")
    if units == "billed_units":
        if "units_billed" not in low:
            violations.append("the locked unit is units we billed (units_billed), not units the payer paid")
        if "sem.claim_lines" in low and "is_latest_submission" not in low:
            violations.append("billed units are read from the latest submission so a replacement is not added twice")
    if units == "paid_units":
        if "units_paid" not in low:
            violations.append("the locked unit is units the payer paid (units_paid)")
        if re.search(r"sem\.remit_lines\b", low) and "is_current_adjudication" not in low:
            violations.append("paid units come from current adjudications")
    if re.search(r"\bdenial|\bdenied\b", q) and re.search(r"paid_amount\s*=\s*0", low) and "is_denied" not in low:
        violations.append("$0 paid is not a denial; filter is_denied (recorded status 4 or the signal rule)")
    return violations
