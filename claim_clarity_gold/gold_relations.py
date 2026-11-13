"""Gold v2: state tables and event tables, derived from what people ask of claim data (see README).

Every relation is defined over the semantic views (claude/schema/02_semantic_layer_views.sql), which are defined
over silver, so gold holds nothing silver does not.

    event   one row per thing that happened; never changed once written; appended a batch at a time
    state   one row per claim (and payer) holding the current truth; a batch replaces the rows of the claims it names

Event rows carry no column a later file can change: "the current adjudication" and "the latest submission" are
columns of the state tables.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EventRelation:
    grain: str
    semantic_relation: str           # sem.<this> has the rows
    columns: list[str]               # column names of the semantic relation, or "<expression> AS <name>"
    month_column: str                # the month the event happened in: the table is partitioned by it
    sort_columns: str


EVENT_RELATIONS: dict[str, EventRelation] = {
    "claim_submissions": EventRelation(
        grain="one 837 claim submission (CLM)", semantic_relation="claims",
        columns=["claim_id", "claim_key", "frequency_code", "payer_key", "payer_name", "payer_class",
                 "payer_responsibility", "filing_indicator", "charge_amount", "place_of_service", "line_count",
                 "principal_diagnosis_code", "diagnosis_codes", "service_date_from", "service_date_to",
                 "service_month", "submission_date", "submission_month", "billing_npi", "rendering_npi",
                 "referring_npi", "source_file_id"],
        month_column="submission_month", sort_columns="claim_key, claim_id"),
    "claim_lines": EventRelation(
        grain="one 837 service line (SV1) of a submission", semantic_relation="claim_lines",
        columns=["claim_line_id", "claim_id", "claim_key", "line_number", "procedure_code", "modifier_1",
                 "modifier_2", "modifier_3", "modifier_4", "line_charge_amount", "units_billed",
                 "place_of_service", "diagnosis_pointers", "service_date", "service_month", "submission_date",
                 "date_trunc('month', submission_date)::DATE AS submission_month", "rendering_npi",
                 "source_file_id"],
        month_column="submission_month", sort_columns="claim_key, claim_id, claim_line_id"),
    "remit_claims": EventRelation(
        grain="one 835 claim payment (CLP): a payer's adjudication of a claim, or its reversal",
        semantic_relation="remit_claims",
        columns=["remit_claim_id", "claim_key", "payer_key", "payer_name", "payer_class", "payer_sequence",
                 "claim_status_code", "claim_status", "is_reversal", "is_duplicate_claim_denial", "is_denied",
                 "payer_claim_control_number", "charge_amount", "paid_amount", "patient_responsibility_amount",
                 "co_amount", "pr_amount", "oa_amount", "pi_amount", "adjustment_codes", "payment_date",
                 "payment_month", "check_or_eft_number", "service_date_from", "service_date_to", "service_month",
                 "payer_received_date", "source_file_id"],
        month_column="payment_month", sort_columns="claim_key, remit_claim_id"),
    "remit_lines": EventRelation(
        grain="one 835 service line (SVC) of a claim payment", semantic_relation="remit_lines",
        columns=["remit_line_id", "remit_claim_id", "claim_key", "payer_key", "payer_name", "payer_class",
                 "is_reversal", "procedure_code", "modifier_1", "modifier_2", "adjudicated_procedure_code",
                 "procedure_changed_by_payer", "revenue_code", "line_charge_amount", "line_paid_amount",
                 "line_allowed_amount", "units_paid", "units_billed", "line_co_amount", "line_pr_amount",
                 "line_adjustment_codes", "line_remark_codes", "line_is_denied", "service_date", "service_month",
                 "payment_date", "payment_month", "source_file_id"],
        month_column="payment_month", sort_columns="claim_key, remit_claim_id, remit_line_id"),
    "adjustments": EventRelation(
        grain="one 835 adjustment (a CAS group / reason / amount) on a claim payment or a service line",
        semantic_relation="adjustments",
        columns=["adjustment_id", "remit_claim_id", "remit_line_id", "claim_key", "payer_key", "payer_class",
                 "is_reversal", "adjustment_level", "group_code", "carc_code", "carc_description", "carc_category",
                 "is_denial_carc", "adjustment_amount", "procedure_code", "payment_date", "payment_month",
                 "source_file_id"],
        month_column="payment_month", sort_columns="remit_claim_id, adjustment_id"),
    "payments": EventRelation(
        grain="one 835 payment (BPR / TRN): a check or EFT", semantic_relation="payments",
        columns=["payment_id", "payer_key", "payer_name", "payer_class", "check_or_eft_number", "payment_method",
                 "total_payment_amount", "payment_date", "payment_month", "claim_count", "reversal_count",
                 "claims_paid_amount", "provider_adjustment_amount", "is_balanced", "source_file_id"],
        month_column="payment_month", sort_columns="payment_date, payment_id"),
    "provider_adjustments": EventRelation(
        grain="one 835 provider-level adjustment (PLB)", semantic_relation="provider_adjustments",
        columns=["provider_adjustment_id", "payment_id", "payer_key", "payer_name", "plb_reason_code",
                 "plb_reason_description", "reference_id", "provider_adjustment_amount", "fiscal_period_date",
                 "payment_date", "payment_month"],
        month_column="payment_month", sort_columns="payment_id, provider_adjustment_id"),
}

STATE_MONTH_COLUMN = "first_activity_month"      # both state tables are partitioned by it

# One row per claim and payer. {claims} and {remit_claims} name the semantic relations (which know which
# submission is the latest and which adjudication is current).
CLAIM_PAYERS_SQL = """
WITH submissions AS (
    SELECT claim_key, payer_key, any_value(payer_name) AS payer_name, any_value(payer_class) AS payer_class,
           min(payer_responsibility) AS payer_responsibility,      -- 'P' if the payer was ever billed as primary
           min(submission_date) AS first_submission_date, max(submission_date) AS last_submission_date,
           count(*) AS submission_count
    FROM {claims} GROUP BY claim_key, payer_key),
remits AS (
    SELECT claim_key, payer_key, any_value(payer_name) AS payer_name, any_value(payer_class) AS payer_class,
           min(payment_date) AS first_remit_date, max(payment_date) AS last_remit_date, count(*) AS remit_count,
           count(*) FILTER (WHERE is_reversal) AS reversal_count,
           any_value(remit_claim_id) FILTER (WHERE is_current_adjudication) AS current_remit_claim_id,
           any_value(payment_date) FILTER (WHERE is_current_adjudication) AS current_payment_date,
           any_value(claim_status_code) FILTER (WHERE is_current_adjudication) AS current_claim_status_code,
           any_value(charge_amount) FILTER (WHERE is_current_adjudication) AS current_charge_amount,
           any_value(paid_amount) FILTER (WHERE is_current_adjudication) AS current_paid_amount,
           any_value(patient_responsibility_amount) FILTER (WHERE is_current_adjudication)
               AS current_patient_responsibility_amount,
           any_value(co_amount) FILTER (WHERE is_current_adjudication) AS current_co_amount,
           any_value(is_denied) FILTER (WHERE is_current_adjudication) AS current_is_denied
    FROM {remit_claims} GROUP BY claim_key, payer_key)
SELECT claim_key, payer_key, coalesce(s.payer_name, r.payer_name) AS payer_name,
       coalesce(s.payer_class, r.payer_class) AS payer_class, s.payer_responsibility,
       date_trunc('month', least(s.first_submission_date, r.first_remit_date))::DATE AS first_activity_month,
       s.first_submission_date, s.last_submission_date, coalesce(s.submission_count, 0) AS submission_count,
       r.first_remit_date, r.last_remit_date, coalesce(r.remit_count, 0) AS remit_count,
       coalesce(r.reversal_count, 0) AS reversal_count,
       r.current_remit_claim_id IS NOT NULL AS has_current_adjudication, r.current_remit_claim_id,
       r.current_payment_date, r.current_claim_status_code, r.current_charge_amount, r.current_paid_amount,
       r.current_patient_responsibility_amount, r.current_co_amount, r.current_is_denied
FROM submissions s FULL JOIN remits r USING (claim_key, payer_key)
"""

# One row per claim: its latest submission, that submission's payer and the payer's current adjudication, and
# totals over every payer. {latest_submissions} is the latest submission of each claim (the semantic relation claims
# WHERE is_latest_submission), {claim_payers} the relation above.
CLAIMS_SQL = """
WITH latest AS (SELECT * FROM {latest_submissions}),
over_payers AS (
    SELECT claim_key, min(first_activity_month) AS first_activity_month,
           min(first_submission_date) AS first_submission_date, max(last_submission_date) AS last_submission_date,
           sum(submission_count) AS submission_count, count(*) AS payer_count,
           min(first_remit_date) AS first_remit_date, max(last_remit_date) AS last_remit_date,
           count(*) FILTER (WHERE has_current_adjudication) AS payers_with_current_adjudication,
           sum(current_paid_amount) AS total_paid_amount,
           sum(reversal_count) AS reversal_count
    FROM {claim_payers} GROUP BY claim_key)
SELECT o.claim_key, o.first_activity_month, l.claim_id AS latest_claim_id, l.frequency_code, l.charge_amount,
       l.line_count, l.place_of_service, l.principal_diagnosis_code, l.diagnosis_codes, l.service_date_from,
       l.service_date_to, l.service_month, l.billing_npi, l.rendering_npi, l.referring_npi,
       l.payer_key, l.payer_name, l.payer_class, l.payer_responsibility,
       o.first_submission_date, o.last_submission_date, o.submission_count, o.payer_count,
       coalesce(p.has_current_adjudication, false) AS is_adjudicated, p.current_remit_claim_id,
       p.current_payment_date AS payment_date, p.current_paid_amount AS paid_amount,
       p.current_patient_responsibility_amount AS patient_responsibility_amount,
       p.current_is_denied AS is_denied,
       o.first_remit_date, o.last_remit_date, o.payers_with_current_adjudication, o.total_paid_amount,
       o.reversal_count
FROM over_payers o LEFT JOIN latest l USING (claim_key)
LEFT JOIN {claim_payers} p ON p.claim_key = o.claim_key AND p.payer_key = l.payer_key
"""
STATE_RELATIONS = {"claim_payers": "one claim and one payer", "claims": "one claim"}
STATE_SORT_COLUMNS = {"claim_payers": "claim_key, payer_key", "claims": "claim_key"}
