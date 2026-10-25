"""Closed catalog the A03 compiler will turn into SQL.

Populations, links, and columns are the only names the model may use. The compiler
never accepts a table, join, or expression the model invented. Column names match
``claude/schema/02_semantic_layer_views.sql``; derived columns are computed by the
compiler and are not view columns.
"""
from __future__ import annotations

from dataclasses import dataclass


BOOLEAN_FIELDS = frozenset({
    "is_current_adjudication", "is_reversal", "is_duplicate_claim_denial", "is_denied",
    "is_denied_recorded", "is_denied_signal_v3", "is_latest_submission", "is_denied_recorded",
    "has_remit", "ever_reversed", "any_payer_denied", "has_line_remit", "line_is_denied",
    "claim_is_denied", "procedure_changed_by_payer", "is_denial_carc", "is_balanced",
    "is_principal",
})

DATE_FIELDS = frozenset({
    "payment_date", "payment_month", "service_date", "service_date_from", "service_date_to",
    "service_month", "submission_date", "submission_month", "production_date", "payer_received_date",
    "fiscal_period_date", "first_payment_date", "last_payment_date", "first_submission_date",
    "first_submission_month", "first_remit_date",
})

LIST_FIELDS = frozenset({
    "adjustment_codes", "diagnosis_codes", "line_adjustment_codes", "line_remark_codes",
    "names_as_sent", "ids_as_sent", "diagnosis_pointers",
})

# Claim-level measures that repeat once per adjustment row. Summing them after
# adjudication_adjustments is a fan-out, so the compiler rejects it.
CLAIM_LEVEL_MEASURES = frozenset({
    "paid_amount", "charge_amount", "patient_responsibility_amount",
    "co_amount", "pr_amount", "oa_amount", "pi_amount",
    "is_denied", "is_denied_recorded", "is_denied_signal_v3",
})

FIELD_HINTS = {
    "carc_code": "use population current_adjustments or primary_current_adjustments, or link adjudication_adjustments",
    "group_code": "use population current_adjustments or primary_current_adjustments, or link adjudication_adjustments",
    "is_denial_carc": "use link adjudication_adjustments or population current_adjustments",
    "adjustment_amount": "use population current_adjustments or primary_current_adjustments, or link adjudication_adjustments",
    "referring_npi": "use population latest_submissions or submissions, or link primary_billed_attributes",
    "principal_diagnosis_code": "use population latest_submissions, or link primary_billed_attributes",
    "procedure_code": "use population current_service_lines or billed_lines, or link billed_procedures on current_adjudications",
    "plb_reason_code": "use population provider_adjustments",
    "provider_adjustment_amount": "use population provider_adjustments; interest (L6) is stored with the opposite sign, so set sign flip",
    "line_is_denied": "use population current_service_lines; claim-level denials have no service lines",
    "line_paid_amount": "use population current_service_lines",
    "days_first_submission_to_first_remit": "use population primary_remit_lag",
    "first_submission_date": "use population claim_first_submission",
    "first_submission_month": "use population claim_first_submission",
    "primary_payer_name": "filter population secondary_current_for_primary with primary_payer_name equals the primary payer",
    "is_balanced": "use population payments",
    "remark_code": "use population current_remarks",
    "diagnosis_code": "use population latest_diagnoses",
    "modifiers": "filter with op in_modifiers on billed_lines (four modifiers) or current_service_lines (two)",
    "has_remit": "use population submissions, latest_submissions, or claim_first_submission",
    "frequency_code": "on submissions this is the 837 frequency (7 replacement, 8 void); on remit events it is the 835 CLP frequency",
}


@dataclass(frozen=True)
class Population:
    name: str
    description: str
    kind: str                      # table | derived | parameterized
    relation: str | None
    where_sql: str | None          # already qualified with spec_pop; table populations only
    columns: tuple[str, ...]
    modifier_columns: tuple[str, ...] = ()
    virtual: tuple[tuple[str, str], ...] = ()   # (name, sql expr using spec_pop)
    derived_sql: str | None = None
    derived_columns: tuple[str, ...] = ()
    source_views: tuple[str, ...] = ()
    note: str = ""


def _remit_claim_columns() -> tuple[str, ...]:
    return (
        "remit_claim_id", "claim_key", "claim_payer_key", "payer_key", "payer_name", "payer_class",
        "payer_sequence", "claim_status_code", "claim_status", "adjudication_state",
        "is_current_adjudication", "is_reversal", "is_duplicate_claim_denial",
        "is_denied", "is_denied_recorded", "is_denied_signal_v3",
        "payer_claim_control_number", "filing_indicator", "frequency_code",
        "charge_amount", "paid_amount", "patient_responsibility_amount",
        "co_amount", "pr_amount", "oa_amount", "pi_amount", "adjustment_codes",
        "payment_date", "payment_month", "production_date", "check_or_eft_number", "payment_method",
        "service_date_from", "service_date_to", "service_month", "payer_received_date", "source_file_id",
    )


def _adjustment_columns() -> tuple[str, ...]:
    return (
        "adjustment_id", "remit_claim_id", "remit_line_id", "claim_key", "claim_payer_key",
        "payer_key", "payer_name", "payer_class", "is_current_adjudication", "is_reversal",
        "adjudication_state", "adjustment_level", "group_code", "group_description", "responsible_party",
        "carc_code", "carc_description", "carc_category", "is_denial_carc",
        "adjustment_amount", "adjustment_quantity", "procedure_code",
        "payment_date", "payment_month", "service_date", "source_file_id",
    )


_CLAIM_PAYER_KEY = (("claim_payer_key", "spec_pop.claim_key || '|' || spec_pop.payer_key"),)


POPULATIONS: dict[str, Population] = {}


def _add(pop: Population) -> None:
    POPULATIONS[pop.name] = pop


_add(Population(
    "current_adjudications",
    "One current 835 adjudication per claim and payer (is_current_adjudication). Reversals and superseded events are already excluded. This is the population for paid, denied, and patient-responsibility questions.",
    "table", "sem.remit_claims", "spec_pop.is_current_adjudication", _remit_claim_columns(),
    source_views=("sem.remit_claims",),
    note="is_current_adjudication is applied. Do not also filter it.",
))
_add(Population(
    "remit_events",
    "Every 835 claim event, including reversals and superseded adjudications. Use this for reversal counts and for 'how many payers have ever remitted', not for current paid or denial rate.",
    "table", "sem.remit_claims", None, _remit_claim_columns(),
    source_views=("sem.remit_claims",),
))
_add(Population(
    "latest_submissions",
    "One 837 row per claim_key (is_latest_submission). That row can be the secondary payer's submission. Do not use it to decide which payer was primary.",
    "table", "sem.claims", "spec_pop.is_latest_submission", (
        "claim_id", "claim_key", "is_latest_submission", "submission_count", "frequency_code", "frequency",
        "payer_key", "payer_name", "payer_class", "payer_responsibility", "filing_indicator", "charge_amount",
        "place_of_service", "line_count", "principal_diagnosis_code", "diagnosis_codes",
        "service_date_from", "service_date_to", "service_month", "submission_date", "submission_month",
        "billing_npi", "billing_provider_name", "billing_provider_state", "rendering_npi", "referring_npi",
        "has_remit", "ever_reversed", "current_adjudication_count", "latest_claim_status",
        "is_denied", "any_payer_denied", "remit_paid_amount", "remit_patient_responsibility_amount",
        "remit_co_amount", "first_payment_date", "last_payment_date", "days_submission_to_first_remit",
        "source_file_id",
    ), source_views=("sem.claims",),
    note="is_latest_submission is applied. is_denied here follows the primary-or-unknown adjudication, not each payer. days_submission_to_first_remit restarts on each submission; use primary_remit_lag for first submission to first 835.",
))
_add(Population(
    "submissions",
    "Every 837 submission, including resubmissions and secondary bills. Count distinct claim_key when the question asks for claims. frequency_code 7 is a replacement and 8 is a void.",
    "table", "sem.claims", None, POPULATIONS["latest_submissions"].columns,
    source_views=("sem.claims",),
))
_add(Population(
    "current_service_lines",
    "One 835 service line on a current adjudication. Claims denied at claim level have no rows here; billed codes for those claims are on billed_lines.",
    "table", "sem.remit_lines", "spec_pop.is_current_adjudication", (
        "remit_line_id", "remit_claim_id", "claim_key", "claim_payer_key", "payer_key", "payer_name",
        "payer_class", "payer_sequence", "claim_status_code", "claim_status", "adjudication_state",
        "is_current_adjudication", "is_reversal", "claim_is_denied", "procedure_code",
        "modifier_1", "modifier_2", "adjudicated_procedure_code", "procedure_changed_by_payer",
        "procedure_code_type", "revenue_code", "line_charge_amount", "line_paid_amount",
        "line_allowed_amount", "units_paid", "units_billed", "line_co_amount", "line_pr_amount",
        "line_oa_amount", "line_pi_amount", "line_adjustment_codes", "line_remark_codes", "line_is_denied",
        "service_date", "service_month", "payment_date", "payment_month", "line_item_control_number",
        "source_file_id",
    ), modifier_columns=("modifier_1", "modifier_2"), source_views=("sem.remit_lines",),
    note="is_current_adjudication is applied. line_is_denied is the line; claim_is_denied is the claim.",
))
_add(Population(
    "billed_lines",
    "837 service lines on the claim's latest submission. Modifier questions use op in_modifiers. This is what was billed, not what the payer adjudicated.",
    "table", "sem.claim_lines", "spec_pop.is_latest_submission", (
        "claim_line_id", "claim_id", "claim_key", "is_latest_submission", "payer_key", "payer_name",
        "payer_class", "filing_indicator", "line_number", "procedure_code",
        "modifier_1", "modifier_2", "modifier_3", "modifier_4", "procedure_code_type",
        "line_charge_amount", "units_billed", "place_of_service", "diagnosis_pointers",
        "service_date", "service_month", "submission_date", "billing_npi", "rendering_npi",
        "has_line_remit", "remit_match_method", "line_paid_amount", "line_allowed_amount",
        "line_co_amount", "line_pr_amount", "line_is_denied", "line_adjustment_codes", "line_remark_codes",
        "adjudicated_procedure_code", "procedure_changed_by_payer", "payment_date",
        "line_item_control_number", "source_file_id",
    ), modifier_columns=("modifier_1", "modifier_2", "modifier_3", "modifier_4"),
    source_views=("sem.claim_lines",),
    note="is_latest_submission is applied.",
))
_add(Population(
    "current_adjustments",
    "One CAS reason and amount on a current adjudication, claim or line level. A claim with several reasons is several rows. Count distinct remit_claim_id for claims, not count(*).",
    "table", "sem.adjustments", "spec_pop.is_current_adjudication",
    tuple(c for c in _adjustment_columns() if c != "claim_payer_key"),
    virtual=_CLAIM_PAYER_KEY, source_views=("sem.adjustments",),
    note="is_current_adjudication is applied. count(*) counts adjustment rows.",
))
_add(Population(
    "current_remarks",
    "One remark code on a current adjudication.",
    "table", "sem.remarks", "spec_pop.is_current_adjudication", (
        "remark_id", "remit_claim_id", "remit_line_id", "claim_key", "payer_key", "payer_name",
        "is_current_adjudication", "remark_level", "remark_code", "remark_description", "procedure_code",
        "payment_date", "payment_month",
    ), virtual=_CLAIM_PAYER_KEY, source_views=("sem.remarks",),
))
_add(Population(
    "payments",
    "One 835 payment (check, EFT, or zero-pay advice). A re-delivered file is already one row. is_balanced is false when the payment total does not equal claim payments minus provider-level adjustments.",
    "table", "sem.payments", None, (
        "payment_id", "payer_key", "payer_name", "payer_class", "check_or_eft_number", "payment_method",
        "transaction_handling", "total_payment_amount", "payment_date", "payment_month", "claim_count",
        "reversal_count", "claims_paid_amount", "provider_adjustment_amount", "is_balanced", "source_file_id",
    ), source_views=("sem.payments",),
))
_add(Population(
    "provider_adjustments",
    "One PLB reason and amount. Interest is reason L6 and is stored with the opposite sign of a payment to the lab; set sign flip when the question asks for a positive interest amount.",
    "table", "sem.provider_adjustments", None, (
        "provider_adjustment_id", "payment_id", "payer_key", "payer_name", "plb_reason_code",
        "plb_reason_description", "reference_id", "provider_adjustment_amount", "fiscal_period_date",
        "payment_date", "payment_month",
    ), source_views=("sem.provider_adjustments",),
))
_add(Population(
    "latest_diagnoses",
    "One diagnosis code on the latest 837 submission. is_principal marks the principal diagnosis. For a denial rate by principal diagnosis use link primary_billed_attributes instead.",
    "table", "sem.diagnoses", "spec_pop.is_latest_submission", (
        "diagnosis_id", "claim_id", "claim_key", "is_latest_submission", "payer_key", "payer_name",
        "diagnosis_code", "diagnosis_sequence", "is_principal", "service_month", "claim_is_denied",
    ), source_views=("sem.diagnoses",),
))
_add(Population(
    "payers",
    "One canonical payer. Use this to count payers we have a name for. Payment and denial questions belong on current_adjudications.",
    "table", "sem.payers", None, (
        "payer_key", "payer_name", "payer_class", "names_as_sent", "ids_as_sent",
        "claim_submissions", "payments",
    ), source_views=("sem.payers",),
))
_add(Population(
    "reversed_without_current",
    "Claim and payer pairs whose 835 history contains a reversal and no current adjudication (reversed and not corrected).",
    "derived", None, None, ("claim_payer_key", "claim_key", "payer_key"),
    derived_columns=("claim_payer_key", "claim_key", "payer_key"),
    derived_sql="""
SELECT claim_payer_key, claim_key, payer_key
FROM sem.remit_claims
GROUP BY claim_payer_key, claim_key, payer_key
HAVING bool_or(is_reversal) AND NOT bool_or(is_current_adjudication)
""".strip(),
    source_views=("sem.remit_claims",),
    note="one row per reversed claim+payer with no current adjudication.",
))
_add(Population(
    "claim_first_submission",
    "One row per claim_key. first_submission_date is the earliest 837 in the data, not a date before the extract started. has_remit is true if any 835 exists for the claim.",
    "derived", None, None,
    ("claim_key", "first_submission_date", "first_submission_month", "has_remit"),
    derived_columns=("first_submission_date", "first_submission_month", "has_remit"),
    derived_sql="""
SELECT claim_key,
       min(submission_date) AS first_submission_date,
       date_trunc('month', min(submission_date))::DATE AS first_submission_month,
       bool_or(has_remit) AS has_remit
FROM sem.claims
GROUP BY claim_key
""".strip(),
    source_views=("sem.claims",),
))
_add(Population(
    "primary_remit_lag",
    "One row per claim and primary payer. days_first_submission_to_first_remit is the days from the first 837 billed to that payer as primary (payer_responsibility P) to the first 835 from that payer, any status, including a reversal. Claims with no 835 are absent.",
    "derived", None, None, (
        "claim_key", "payer_key", "payer_name", "payer_class",
        "days_first_submission_to_first_remit", "first_submission_date", "first_remit_date",
    ),
    derived_columns=("days_first_submission_to_first_remit", "first_submission_date", "first_remit_date"),
    derived_sql="""
SELECT s.claim_key, s.payer_key, r.payer_name, r.payer_class,
       date_diff('day', s.first_submission_date, r.first_remit_date) AS days_first_submission_to_first_remit,
       s.first_submission_date, r.first_remit_date
FROM (
    SELECT claim_key, payer_key, min(submission_date) AS first_submission_date
    FROM sem.claims
    WHERE payer_responsibility = 'P'
    GROUP BY claim_key, payer_key
) AS s
JOIN (
    SELECT claim_key, payer_key, min(payer_name) AS payer_name, min(payer_class) AS payer_class,
           min(payment_date) AS first_remit_date
    FROM sem.remit_claims
    GROUP BY claim_key, payer_key
) AS r USING (claim_key, payer_key)
""".strip(),
    source_views=("sem.claims", "sem.remit_claims"),
    note="payer_sequence is not used. Primary comes from payer_responsibility P on the 837.",
))
_add(Population(
    "primary_current_adjustments",
    "CAS rows on the current adjudication of a payer that was billed as primary (payer_responsibility P). Use this for primary-payer deductible and coinsurance, which live on adjustments (group PR), not on patient_responsibility_amount.",
    "derived", None, None, _adjustment_columns(),
    derived_columns=("claim_payer_key",),
    derived_sql="""
SELECT a.*, (a.claim_key || '|' || a.payer_key) AS claim_payer_key
FROM sem.adjustments AS a
JOIN (
    SELECT DISTINCT claim_key, payer_key
    FROM sem.claims
    WHERE payer_responsibility = 'P'
) AS spec_prim
  ON spec_prim.claim_key = a.claim_key AND spec_prim.payer_key = a.payer_key
WHERE a.is_current_adjudication
""".strip(),
    source_views=("sem.adjustments", "sem.claims"),
    note="already restricted to current adjudications of primary-billed payers.",
))
_add(Population(
    "secondary_current_for_primary",
    "Current secondary-payer adjudications (payer_sequence secondary) on claims that were billed to a named primary payer. Requires a filter primary_payer_name equals that payer's stored name. Sums of paid_amount are what the secondary paid, not what the primary paid.",
    "parameterized", None, None, _remit_claim_columns(),
    source_views=("sem.remit_claims", "sem.claims"),
    note="primary_payer_name is a filter, not a column on the result.",
))


PRIMARY_ATTRIBUTE_FIELDS = (
    "referring_npi", "principal_diagnosis_code", "billing_npi", "rendering_npi",
    "place_of_service", "billing_provider_name", "line_count",
)


@dataclass(frozen=True)
class Link:
    name: str
    description: str
    base_populations: frozenset[str]
    adds: tuple[str, ...]
    fanout_claim_measures: bool
    note: str


LINKS: dict[str, Link] = {
    "adjudication_adjustments": Link(
        "adjudication_adjustments",
        "Join the population's adjudication to its CAS rows. Grain becomes the adjustment. count(*) counts reasons, not claims; count distinct remit_claim_id counts claims. Summing paid_amount or charge_amount is rejected because the claim amount would repeat once per reason.",
        frozenset({"current_adjudications", "remit_events"}),
        ("carc_code", "carc_description", "carc_category", "group_code", "group_description",
         "responsible_party", "is_denial_carc", "adjustment_amount", "adjustment_quantity",
         "adjustment_level", "adjustment_id", "remit_line_id", "procedure_code"),
        True,
        "adjustment grain. Claim-level money cannot be summed through this link.",
    ),
    "primary_billed_attributes": Link(
        "primary_billed_attributes",
        "Keep current adjudications whose payer was billed as primary (payer_responsibility P on the 837), and attach 837 attributes that are not on the 835: referring_npi, principal_diagnosis_code, billing_npi, rendering_npi, place_of_service, billing_provider_name, line_count. A claim with two distinct values of a requested attribute is counted once per value. payer_sequence is not how primary is chosen. frequency_code on this population stays the 835 frequency.",
        frozenset({"current_adjudications"}),
        PRIMARY_ATTRIBUTE_FIELDS,
        False,
        "restricted to payers billed with payer_responsibility P.",
    ),
    "billed_procedures": Link(
        "billed_procedures",
        "Attach each distinct procedure_code billed on the claim's latest 837 submission. A claim counts once per billed CPT. Use this for denial rate by CPT billed. Do not use current_service_lines for that question: a claim denied at claim level has no 835 lines.",
        frozenset({"current_adjudications"}),
        ("procedure_code",),
        False,
        "a claim is repeated once per distinct procedure on its latest submission. Summing paid_amount counts the claim's payment once per CPT.",
    ),
}


TEXT_FIELDS = frozenset({
    "remit_claim_id", "claim_key", "claim_payer_key", "payer_key", "payer_name", "payer_class",
    "payer_sequence", "claim_status_code", "claim_status", "adjudication_state",
    "payer_claim_control_number", "filing_indicator", "frequency_code", "frequency",
    "payer_responsibility", "check_or_eft_number", "payment_method", "source_file_id",
    "place_of_service", "principal_diagnosis_code", "billing_npi", "billing_provider_name",
    "billing_provider_state", "rendering_npi", "referring_npi", "latest_claim_status",
    "procedure_code", "modifier_1", "modifier_2", "modifier_3", "modifier_4",
    "adjudicated_procedure_code", "procedure_code_type", "revenue_code", "line_item_control_number",
    "adjustment_level", "group_code", "group_description", "responsible_party", "carc_code",
    "carc_description", "carc_category", "remark_level", "remark_code", "remark_description",
    "plb_reason_code", "plb_reason_description", "reference_id", "diagnosis_code",
    "remit_match_method", "transaction_handling", "payment_id", "claim_id", "claim_line_id",
    "adjustment_id", "remark_id", "diagnosis_id", "provider_adjustment_id", "remit_line_id",
})


def field_kind(name: str) -> str:
    if name in BOOLEAN_FIELDS:
        return "boolean"
    if name in DATE_FIELDS:
        return "date"
    if name in LIST_FIELDS:
        return "list"
    if name in TEXT_FIELDS:
        return "text"
    return "number"


def population_columns(pop: Population) -> tuple[str, ...]:
    virtual_names = tuple(name for name, _expr in pop.virtual)
    return tuple(dict.fromkeys((*pop.columns, *virtual_names)))


def catalog_text() -> str:
    """Model-facing catalog. Generated from the same objects the compiler uses."""
    lines = ["# Populations",
             "Pick one. The compiler applies the population's predicate; do not restate it as a filter.",
             ""]
    for pop in POPULATIONS.values():
        relation = pop.relation or "compiler-owned subquery over sem.*"
        lines.append(f"- {pop.name} ({relation}): {pop.description}")
        lines.append("  columns: " + ", ".join(population_columns(pop)))
        if pop.modifier_columns:
            lines.append("  modifiers: " + ", ".join(pop.modifier_columns) + " (filter with op in_modifiers)")
    lines.append("")
    lines.append("# Links")
    lines.append("At most one. The compiler writes the join. A second grain that is not listed here cannot be expressed.")
    lines.append("")
    for link in LINKS.values():
        bases = ", ".join(sorted(link.base_populations))
        lines.append(f"- {link.name} on {bases}. Adds: {', '.join(link.adds)}. {link.description}")
    lines.append("")
    lines.append("# Aggregations")
    lines.append("count, count_distinct, sum, avg, min, max, ratio.")
    lines.append("ratio.scale is fraction (default) or percent. ratio is numerator over denominator, with a zero denominator returning null.")
    lines.append("sum.sign is as_stored (default) or flip.")
    lines.append("avg of a boolean averages 1 and 0 and skips nulls.")
    lines.append("A scalar sum with no matching rows is 0. Set coalesce_zero true to do that for a grouped sum too.")
    lines.append("")
    lines.append("# Filters")
    lines.append("op is one of = != > >= < <= in not_in between is_true is_false is_null is_not_null in_modifiers.")
    lines.append("Date values are YYYY-MM-DD. payment_month and submission_month are the first day of the month.")
    lines.append("in_modifiers tests every modifier column on the population. field must be modifiers.")
    lines.append("")
    lines.append("# Grain, time, ties, rounding")
    lines.append("grain is a column or {field, bucket} with bucket day, week, month, quarter, or year.")
    lines.append("time_window is {field, start, end, end_exclusive}. end is inclusive unless end_exclusive is true.")
    lines.append("limit requires order_by and ties. ties strict keeps LIMIT N after the full order_by, so put the dimension last as a tie-break. ties include_boundary ranks only on the ordered metrics. Grain columns in order_by are not part of that rank, so every row tied with the Nth is kept and the result can be longer than the limit.")
    lines.append("rounding is {digits, aliases}. aliases defaults to every sum, avg, and ratio, not to counts. Rounding is applied once, in the select list.")
    lines.append("")
    lines.append("# What this spec will not express")
    lines.append("Windows, lags, medians, quantiles, existence tests, unnest, and any join that is not a link.")
    lines.append("Those return cannot_express. Patient names, member ids, birth dates, other customers, and writes are refusals, not a SQL fallback.")
    return "\n".join(lines)
