"""The gold schema: seven relations, each a subset of one semantic relation's columns, and one merged from two.

Every gold relation is a projection of the semantic view of the same name (claude/schema/02_semantic_layer_views.sql),
which is defined over silver; gold therefore holds nothing silver does not. A column is in gold when a query of the
workload reads it (claim_clarity_gold/query_workload.py), plus the identifiers that join the relations,
source_file_id, which leads back to the raw file, and the month or date a relation is partitioned by. Relations no workload query needs (remarks, payers, diagnoses) and
columns none reads are left to silver.

claim_payers is merged from claims and remit_claims: a third of the workload joins the two per claim and payer to
ask when a claim was first submitted, whether it has been adjudicated, and what was paid.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldRelation:
    grain: str                       # what one row is
    sort_columns: str                # rows are stored in this order
    columns: list[str]
    partition_by: str | None = None  # the incremental store keeps a file set per value of this


GOLD_RELATIONS: dict[str, GoldRelation] = {
    "remit_claims": GoldRelation(
        grain="one 835 claim payment (CLP): a payer's adjudication of a claim",
        sort_columns="claim_key, remit_claim_id",
        columns=[
        "remit_claim_id", "claim_key", "claim_payer_key", "payer_key", "payer_name", "payer_class",
        "payer_sequence", "claim_status_code", "adjudication_state", "is_current_adjudication",
        "is_reversal", "is_duplicate_claim_denial", "is_denied", "is_denied_recorded",
        "is_denied_signal_v3", "payer_claim_control_number", "charge_amount", "paid_amount",
        "patient_responsibility_amount", "co_amount", "pr_amount", "oa_amount", "pi_amount",
        "payment_date", "payment_month", "check_or_eft_number", "service_date_from", "service_month",
        "source_file_id"],
        partition_by="payment_month"),
    "remit_lines": GoldRelation(
        grain="one 835 service line (SVC) of a claim payment",
        sort_columns="claim_key, remit_claim_id, remit_line_id",
        columns=[
        "remit_line_id", "remit_claim_id", "claim_key", "payer_key", "payer_name", "payer_class",
        "payer_sequence", "is_current_adjudication", "procedure_code", "modifier_1", "modifier_2",
        "adjudicated_procedure_code", "procedure_changed_by_payer", "line_charge_amount",
        "line_paid_amount", "line_allowed_amount", "units_paid", "units_billed",
        "line_adjustment_codes", "line_is_denied", "payment_month", "source_file_id"],
        partition_by="payment_month"),
    "claims": GoldRelation(
        grain="one 837P claim submission (CLM)",
        sort_columns="claim_key, claim_id",
        columns=[
        "claim_id", "claim_key", "is_latest_submission", "frequency_code", "payer_key", "payer_name",
        "payer_class", "payer_responsibility", "charge_amount", "line_count",
        "principal_diagnosis_code", "service_date_from", "service_month", "submission_date",
        "submission_month", "referring_npi", "source_file_id"],
        partition_by="submission_month"),
    "claim_lines": GoldRelation(
        grain="one 837P service line (SV1) of a submission",
        sort_columns="claim_key, claim_id, claim_line_id",
        columns=[
        "claim_line_id", "claim_id", "claim_key", "is_latest_submission", "procedure_code",
        "modifier_1", "modifier_2", "modifier_3", "modifier_4", "line_charge_amount", "units_billed",
        "service_date", "service_month", "source_file_id", "submission_date"],
        partition_by="year(submission_date), month(submission_date)"),
    "adjustments": GoldRelation(
        grain="one 835 adjustment (a CAS group / reason / amount) on a claim payment or a service line",
        sort_columns="remit_claim_id, adjustment_id",
        columns=[
        "adjustment_id", "remit_claim_id", "remit_line_id", "claim_key", "payer_key", "payer_class",
        "is_current_adjudication", "group_code", "carc_code", "is_denial_carc", "adjustment_amount",
        "source_file_id", "payment_month"],
        partition_by="payment_month"),
    "payments": GoldRelation(
        grain="one 835 payment (BPR / TRN): a check or EFT",
        sort_columns="payment_date, payment_id",
        columns=[
        "payment_id", "payer_class", "payment_method", "total_payment_amount", "payment_date",
        "payment_month", "reversal_count", "claims_paid_amount", "provider_adjustment_amount",
        "source_file_id"],
        partition_by="payment_month"),
    "provider_adjustments": GoldRelation(
        grain="one 835 provider-level adjustment (PLB)",
        sort_columns="payment_id, provider_adjustment_id",
        columns=[
        "provider_adjustment_id", "payment_id", "payer_key", "plb_reason_code", "reference_id",
        "provider_adjustment_amount", "payment_date"]),
}

# One row per claim and payer: what was submitted to the payer and what the payer has answered. Defined over the
# gold relations claims and remit_claims, named by the two placeholders.
CLAIM_PAYERS_COLUMNS = [
    "claim_key", "payer_key", "payer_class", "first_activity_month", "first_submission_date",
    "first_primary_submission_date", "submission_count", "claim_charge_amount", "claim_frequency_code",
    "first_remit_date", "remit_count", "has_current_adjudication", "current_payment_date", "current_paid_amount",
    "current_is_denied"]
CLAIM_PAYERS_PARTITION_BY = "first_activity_month"
CLAIM_PAYERS_SQL = """
WITH latest AS (SELECT claim_key, charge_amount, frequency_code FROM {claims} WHERE is_latest_submission),
submissions AS (
    SELECT claim_key, payer_key, any_value(payer_class) AS payer_class,
           min(submission_date) AS first_submission_date,
           min(submission_date) FILTER (WHERE payer_responsibility = 'P') AS first_primary_submission_date,
           count(*) AS submission_count
    FROM {claims} GROUP BY claim_key, payer_key),
remits AS (
    SELECT claim_key, payer_key, any_value(payer_class) AS payer_class,
           min(payment_date) AS first_remit_date, count(*) AS remit_count,
           count(*) FILTER (WHERE is_current_adjudication) > 0 AS has_current_adjudication,
           any_value(payment_date) FILTER (WHERE is_current_adjudication) AS current_payment_date,
           any_value(paid_amount) FILTER (WHERE is_current_adjudication) AS current_paid_amount,
           any_value(is_denied) FILTER (WHERE is_current_adjudication) AS current_is_denied
    FROM {remit_claims} GROUP BY claim_key, payer_key)
SELECT claim_key, payer_key, coalesce(s.payer_class, r.payer_class) AS payer_class,
       date_trunc('month', least(s.first_submission_date, r.first_remit_date))::DATE AS first_activity_month,
       s.first_submission_date, s.first_primary_submission_date, coalesce(s.submission_count, 0) AS submission_count,
       l.charge_amount AS claim_charge_amount, l.frequency_code AS claim_frequency_code,
       r.first_remit_date, coalesce(r.remit_count, 0) AS remit_count,
       coalesce(r.has_current_adjudication, false) AS has_current_adjudication,
       r.current_payment_date, r.current_paid_amount, r.current_is_denied
FROM submissions s FULL JOIN remits r USING (claim_key, payer_key) LEFT JOIN latest l USING (claim_key)
"""
