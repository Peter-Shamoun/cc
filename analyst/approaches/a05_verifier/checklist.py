"""The critic's rubric: four checks over view invariants.

The bullets are properties of the sem.* views and the basic glossary (grain, population,
dates, units). They are not the screen-1 pitfalls essay, and they do not name a question
id, a payer, or a threshold from the bench. The rendered text is what the critic model
sees; hash it into the run manifest so a scored run records the exact rubric.
"""
from __future__ import annotations

import hashlib

CHECK_NAMES = ("semantics", "population", "units", "invariants")

# Each check is a heading plus the objections that check is allowed to raise.
# Wording is the prompt. Edit it here, not in a second copy in the doc.
CHECKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "semantics": (
        "Does this SQL answer the question that was asked?",
        (
            "The selected measures, the group-by, and the filters have to answer the question's wording. "
            "A different measure, a swapped pair of numbers, or a missing group is an objection.",
            "A ranking has to ORDER BY the measure the question names (and the tie-break it names) and "
            "LIMIT to the number it asks for.",
            "An extra label column is fine. Object to an extra number that states a different quantity.",
            "Judge the SQL and the result. A plan that is only less specific than a SQL whose result "
            "answers the question is not an objection. A plan that contradicts the SQL on grain, "
            "population, time basis, or units is an objection.",
        ),
    ),
    "population": (
        "Is the set of rows the set the question named?",
        (
            "A claim is one claim_key. sem.claims and sem.claim_lines have one row per 837 submission. "
            "Count distinct claim_key when the question asks for claims. is_latest_submission keeps one "
            "row per claim_key, and that row can be a secondary payer's submission.",
            "Primary payer means payer_responsibility = 'P' on the submission. payer_sequence on "
            "sem.remit_claims is 'unknown' for denials and reversals; it does not identify the primary payer.",
            "Money paid, patient responsibility, and denials come from current adjudications "
            "(is_current_adjudication). A reversal is not an additional payment. A duplicate-claim denial "
            "(is_duplicate_claim_denial, CARC 18) does not replace the original adjudication.",
            "is_denied is a recorded denial or the signal-v3 denial in the glossary. A paid amount of zero "
            "is not, by itself, a denial.",
            "sem.remit_lines has no row when the payer denies the whole claim. Billed procedure codes and "
            "units live on sem.claim_lines.",
            "The extract starts at the lab's first delivered file. Claims billed before that appear only as "
            "835 rows, and some claims appear only as a later secondary or corrected submission. When the "
            "question states that population, accept SQL that follows it. Object when the SQL drops or "
            "invents rows against the population the question stated.",
        ),
    ),
    "units": (
        "Are the numbers in the unit the question asked for?",
        (
            "Dollars stay dollars. Rounding at the end, to the precision the question asks for, is fine.",
            "A percentage is 100.0 times the numerator divided by the denominator. Integer division "
            "truncates and is an objection. A rate the question does not call a percentage stays a fraction "
            "in 0..1 (denial rate in the glossary is that fraction).",
            "Prompt-pay interest asked for as a positive amount is the negation of the negative PLB amount.",
            "Claims, submissions, service lines, and payments are different units. Object when the SQL's "
            "unit is not the unit the question named.",
        ),
    ),
    "invariants": (
        "Does the SQL respect the view grain and the read-only session?",
        (
            "Joining line rows to claim-level amounts repeats the claim amount once per line. Aggregate "
            "the lines before that join.",
            "The sem views are already one row per stored event, and re-delivered 835 files are already "
            "de-duplicated. A further DISTINCT that drops events the question asked to count is an objection.",
            "Use the date the question names. payment_date and payment_month are the 835 payment, "
            "service_date and service_month are the date of service, and submission_date is the 837. "
            "Grouping or filtering on a different one is an objection.",
            "The query has to be one read-only SELECT over the views in this session (sem.*, and lossless.* "
            "only when the session is escalated). A question that asks for patient names, member ids, birth "
            "dates, addresses, another customer, or a write should have been refused before this review. "
            "If that query reached you, object.",
            "row_count_returned is capped by the SQL guard. A result that hit the guard limit is not evidence "
            "that the population has that many rows. Zero rows is a reason to look at the filters; it is an "
            "objection only when those filters are narrower than the question. A true empty total is acceptable.",
        ),
    ),
}


def render_checklist() -> str:
    """The critic system-prompt section. Stable bytes: the manifest stores its sha256."""
    lines = ["Critic checklist (a05). Object if any check fails. Use the glossary's definitions."]
    for name in CHECK_NAMES:
        title, bullets = CHECKS[name]
        lines.append(f"## {name}: {title}")
        lines.extend(f"- {bullet}" for bullet in bullets)
    return "\n".join(lines)


def checklist_sha256() -> str:
    return hashlib.sha256(render_checklist().encode()).hexdigest()
