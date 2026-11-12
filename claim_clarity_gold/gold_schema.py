"""The gold schema: seven relations, each a subset of one semantic relation's columns.

Every gold relation is a projection of the semantic view of the same name (claude/schema/02_semantic_layer_views.sql),
which is defined over silver; gold therefore holds nothing silver does not. A column is in gold when a query of the
workload reads it (claim_clarity_gold/query_workload.py), plus the identifiers that join the relations and
source_file_id, which leads back to the raw file. Relations no workload query needs (remarks, payers, diagnoses) and
columns none reads are left to silver.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GoldRelation:
    grain: str                       # what one row is
    sort_columns: str                # rows are stored in this order
    columns: list[str]


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
        "source_file_id"]),
    "remit_lines": GoldRelation(
        grain="one 835 service line (SVC) of a claim payment",
        sort_columns="claim_key, remit_claim_id, remit_line_id",
        columns=[
        "remit_line_id", "remit_claim_id", "claim_key", "payer_key", "payer_name", "payer_class",
        "payer_sequence", "is_current_adjudication", "procedure_code", "modifier_1", "modifier_2",
        "adjudicated_procedure_code", "procedure_changed_by_payer", "line_charge_amount",
        "line_paid_amount", "line_allowed_amount", "units_paid", "units_billed",
        "line_adjustment_codes", "line_is_denied", "payment_month", "source_file_id"]),
    "claims": GoldRelation(
        grain="one 837P claim submission (CLM)",
        sort_columns="claim_key, claim_id",
        columns=[
        "claim_id", "claim_key", "is_latest_submission", "frequency_code", "payer_key", "payer_name",
        "payer_class", "payer_responsibility", "charge_amount", "line_count",
        "principal_diagnosis_code", "service_date_from", "service_month", "submission_date",
        "submission_month", "referring_npi", "source_file_id"]),
    "claim_lines": GoldRelation(
        grain="one 837P service line (SV1) of a submission",
        sort_columns="claim_key, claim_id, claim_line_id",
        columns=[
        "claim_line_id", "claim_id", "claim_key", "is_latest_submission", "procedure_code",
        "modifier_1", "modifier_2", "modifier_3", "modifier_4", "line_charge_amount", "units_billed",
        "service_date", "service_month", "source_file_id"]),
    "adjustments": GoldRelation(
        grain="one 835 adjustment (a CAS group / reason / amount) on a claim payment or a service line",
        sort_columns="remit_claim_id, adjustment_id",
        columns=[
        "adjustment_id", "remit_claim_id", "remit_line_id", "claim_key", "payer_key", "payer_class",
        "is_current_adjudication", "group_code", "carc_code", "is_denial_carc", "adjustment_amount",
        "source_file_id"]),
    "payments": GoldRelation(
        grain="one 835 payment (BPR / TRN): a check or EFT",
        sort_columns="payment_date, payment_id",
        columns=[
        "payment_id", "payer_class", "payment_method", "total_payment_amount", "payment_date",
        "payment_month", "reversal_count", "claims_paid_amount", "provider_adjustment_amount",
        "source_file_id"]),
    "provider_adjustments": GoldRelation(
        grain="one 835 provider-level adjustment (PLB)",
        sort_columns="payment_id, provider_adjustment_id",
        columns=[
        "provider_adjustment_id", "payment_id", "payer_key", "plb_reason_code", "reference_id",
        "provider_adjustment_amount", "payment_date"]),
}
