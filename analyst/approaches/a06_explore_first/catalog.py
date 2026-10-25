"""Column roles and join edges for the sem.* semantic layer.

The names are the output columns of claude/schema/02_semantic_layer_views.sql. Roles decide
which probe is cheap enough to run. Keys and identifiers are counted, never listed: a
min() or a distinct dump would put a patient-control number or an NPI into the notes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

ROLES = frozenset({"enum", "boolean", "date", "measure", "key", "identifier", "code", "text", "list"})
# Histogram these in the summary scan. They are small by construction (status, flag, group).
HISTOGRAM_ROLES = frozenset({"enum", "boolean"})
# List stored values only after the summary shows the column is small.
FOLLOWUP_VALUE_ROLES = frozenset({"code", "text"})
RANGE_ROLES = frozenset({"date", "measure"})
NEVER_LIST_ROLES = frozenset({"key", "identifier", "list"})
LITERAL_ROLES = frozenset({"enum", "code", "text"})
JOIN_ROLES = frozenset({"key", "identifier"})


@dataclass(frozen=True)
class Column:
    name: str
    role: str


@dataclass(frozen=True)
class View:
    name: str
    grain: str
    columns: tuple[Column, ...]

    def column(self, name: str) -> Column | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None


@dataclass(frozen=True)
class JoinEdge:
    left_view: str
    left_column: str
    right_view: str
    right_column: str
    description: str


def _view(name: str, grain: str, spec: str) -> View:
    columns = []
    for line in spec.strip().splitlines():
        column_name, role = line.split()
        if role not in ROLES:
            raise ValueError(f"{name}.{column_name} has unknown role {role!r}")
        if not IDENTIFIER.fullmatch(column_name):
            raise ValueError(f"{name}.{column_name} is not a plain identifier")
        columns.append(Column(column_name, role))
    names = [column.name for column in columns]
    if len(names) != len(set(names)):
        raise ValueError(f"{name} repeats a column name")
    return View(name, grain, tuple(columns))


VIEWS: tuple[View, ...] = (
    _view("sem.remit_claims", "one 835 claim adjudication event, including reversals and superseded events", """
        remit_claim_id key
        claim_key key
        claim_payer_key key
        payer_key key
        payer_name text
        payer_class enum
        payer_sequence enum
        claim_status_code enum
        claim_status enum
        adjudication_state enum
        is_current_adjudication boolean
        is_reversal boolean
        is_duplicate_claim_denial boolean
        is_denied boolean
        is_denied_recorded boolean
        is_denied_signal_v3 boolean
        payer_claim_control_number identifier
        filing_indicator enum
        frequency_code enum
        charge_amount measure
        paid_amount measure
        patient_responsibility_amount measure
        co_amount measure
        pr_amount measure
        oa_amount measure
        pi_amount measure
        adjustment_codes list
        payment_date date
        payment_month date
        production_date date
        check_or_eft_number identifier
        payment_method enum
        service_date_from date
        service_date_to date
        service_month date
        payer_received_date date
        source_file_id identifier
    """),
    _view("sem.remit_lines", "one 835 service line with its claim context", """
        remit_line_id key
        remit_claim_id key
        claim_key key
        claim_payer_key key
        payer_key key
        payer_name text
        payer_class enum
        payer_sequence enum
        claim_status_code enum
        claim_status enum
        adjudication_state enum
        is_current_adjudication boolean
        is_reversal boolean
        claim_is_denied boolean
        procedure_code code
        modifier_1 code
        modifier_2 code
        adjudicated_procedure_code code
        procedure_changed_by_payer boolean
        procedure_code_type enum
        revenue_code code
        line_charge_amount measure
        line_paid_amount measure
        line_allowed_amount measure
        units_paid measure
        units_billed measure
        line_co_amount measure
        line_pr_amount measure
        line_oa_amount measure
        line_pi_amount measure
        line_adjustment_codes list
        line_remark_codes list
        line_is_denied boolean
        service_date date
        service_month date
        payment_date date
        payment_month date
        line_item_control_number identifier
        source_file_id identifier
    """),
    _view("sem.claims", "one 837P claim submission; is_latest_submission keeps one row per claim_key", """
        claim_id key
        claim_key key
        is_latest_submission boolean
        submission_count measure
        frequency_code enum
        frequency enum
        payer_key key
        payer_name text
        payer_class enum
        payer_responsibility enum
        filing_indicator enum
        charge_amount measure
        place_of_service enum
        line_count measure
        principal_diagnosis_code code
        diagnosis_codes list
        service_date_from date
        service_date_to date
        service_month date
        submission_date date
        submission_month date
        billing_npi identifier
        billing_provider_name text
        billing_provider_state enum
        rendering_npi identifier
        referring_npi identifier
        has_remit boolean
        ever_reversed boolean
        current_adjudication_count measure
        latest_claim_status enum
        is_denied boolean
        any_payer_denied boolean
        remit_paid_amount measure
        remit_patient_responsibility_amount measure
        remit_co_amount measure
        first_payment_date date
        last_payment_date date
        days_submission_to_first_remit measure
        source_file_id identifier
    """),
    _view("sem.claim_lines", "one 837P service line with its matched primary 835 line outcome", """
        claim_line_id key
        claim_id key
        claim_key key
        is_latest_submission boolean
        payer_key key
        payer_name text
        payer_class enum
        filing_indicator enum
        line_number measure
        procedure_code code
        modifier_1 code
        modifier_2 code
        modifier_3 code
        modifier_4 code
        procedure_code_type enum
        line_charge_amount measure
        units_billed measure
        place_of_service enum
        diagnosis_pointers list
        service_date date
        service_month date
        submission_date date
        billing_npi identifier
        rendering_npi identifier
        has_line_remit boolean
        remit_match_method enum
        line_paid_amount measure
        line_allowed_amount measure
        line_co_amount measure
        line_pr_amount measure
        line_is_denied boolean
        line_adjustment_codes list
        line_remark_codes list
        adjudicated_procedure_code code
        procedure_changed_by_payer boolean
        payment_date date
        line_item_control_number identifier
        source_file_id identifier
    """),
    _view("sem.adjustments", "one CAS group/reason/amount on an 835 claim or line", """
        adjustment_id key
        remit_claim_id key
        remit_line_id key
        claim_key key
        payer_key key
        payer_name text
        payer_class enum
        is_current_adjudication boolean
        is_reversal boolean
        adjudication_state enum
        adjustment_level enum
        group_code enum
        group_description text
        responsible_party enum
        carc_code code
        carc_description text
        carc_category enum
        is_denial_carc boolean
        adjustment_amount measure
        adjustment_quantity measure
        procedure_code code
        payment_date date
        payment_month date
        service_date date
        source_file_id identifier
    """),
    _view("sem.remarks", "one RARC on an 835 claim or line", """
        remark_id key
        remit_claim_id key
        remit_line_id key
        claim_key key
        payer_key key
        payer_name text
        is_current_adjudication boolean
        remark_level enum
        remark_code code
        remark_description text
        procedure_code code
        payment_date date
        payment_month date
    """),
    _view("sem.payments", "one 835 payment (check, EFT, or advice)", """
        payment_id key
        payer_key key
        payer_name text
        payer_class enum
        check_or_eft_number identifier
        payment_method enum
        transaction_handling enum
        total_payment_amount measure
        payment_date date
        payment_month date
        claim_count measure
        reversal_count measure
        claims_paid_amount measure
        provider_adjustment_amount measure
        is_balanced boolean
        source_file_id identifier
    """),
    _view("sem.provider_adjustments", "one PLB reason/amount pair", """
        provider_adjustment_id key
        payment_id key
        payer_key key
        payer_name text
        plb_reason_code code
        plb_reason_description text
        reference_id identifier
        provider_adjustment_amount measure
        fiscal_period_date date
        payment_date date
        payment_month date
    """),
    _view("sem.diagnoses", "one ICD-10 code on one 837P claim submission", """
        diagnosis_id key
        claim_id key
        claim_key key
        is_latest_submission boolean
        payer_key key
        payer_name text
        diagnosis_code code
        diagnosis_sequence measure
        is_principal boolean
        service_month date
        claim_is_denied boolean
    """),
    _view("sem.payers", "one canonical payer", """
        payer_key key
        payer_name text
        payer_class enum
        names_as_sent list
        ids_as_sent list
        claim_submissions measure
        payments measure
    """),
)

# Fact-table fan-out first. Payer-dimension edges are last so a 3-join cap still
# probes the grain that screen 1's residual errors were about.
JOINS: tuple[JoinEdge, ...] = (
    JoinEdge("sem.claims", "claim_id", "sem.claim_lines", "claim_id",
             "one 837 submission to its billed lines"),
    JoinEdge("sem.remit_claims", "remit_claim_id", "sem.remit_lines", "remit_claim_id",
             "one adjudication to its remit lines"),
    JoinEdge("sem.claims", "claim_key", "sem.remit_claims", "claim_key",
             "submissions to adjudications; many rows per claim_key"),
    JoinEdge("sem.remit_claims", "remit_claim_id", "sem.adjustments", "remit_claim_id",
             "one adjudication to its CAS rows"),
    JoinEdge("sem.claims", "claim_id", "sem.diagnoses", "claim_id",
             "one 837 submission to its diagnosis codes"),
    JoinEdge("sem.payments", "payment_id", "sem.provider_adjustments", "payment_id",
             "one payment to its PLB rows"),
    JoinEdge("sem.remit_claims", "remit_claim_id", "sem.remarks", "remit_claim_id",
             "one adjudication to its RARC rows"),
    JoinEdge("sem.claims", "claim_key", "sem.claim_lines", "claim_key",
             "claim_key across submissions to billed lines"),
    JoinEdge("sem.claim_lines", "claim_key", "sem.remit_lines", "claim_key",
             "billed lines to remit lines; not one row to one row"),
    JoinEdge("sem.claims", "claim_key", "sem.diagnoses", "claim_key",
             "claim_key across submissions to diagnosis codes"),
    JoinEdge("sem.claims", "payer_key", "sem.payers", "payer_key",
             "submission to the payer dimension"),
    JoinEdge("sem.remit_claims", "payer_key", "sem.payers", "payer_key",
             "adjudication to the payer dimension"),
)

VIEW_BY_NAME = {view.name: view for view in VIEWS}


def validate_catalog() -> None:
    if len(VIEW_BY_NAME) != len(VIEWS):
        raise ValueError("duplicate view name in the catalog")
    for edge in JOINS:
        for view_name, column_name in ((edge.left_view, edge.left_column), (edge.right_view, edge.right_column)):
            view = VIEW_BY_NAME.get(view_name)
            if view is None:
                raise ValueError(f"join view {view_name} is not in the catalog")
            column = view.column(column_name)
            if column is None:
                raise ValueError(f"join column {view_name}.{column_name} is not in the catalog")
            if column.role not in JOIN_ROLES:
                raise ValueError(f"join column {view_name}.{column_name} has role {column.role}")


validate_catalog()


def render_catalog() -> str:
    """Every sem view, in full. The writer is not shown a pruned schema."""
    lines = []
    for view in VIEWS:
        rendered = ", ".join(f"{column.name} {column.role}" for column in view.columns)
        lines.append(f"{view.name} - {view.grain}")
        lines.append(f"  {rendered}")
    lines.append("Join edges (probed only when both views are selected):")
    for edge in JOINS:
        lines.append(
            f"  {edge.left_view}.{edge.left_column} = {edge.right_view}.{edge.right_column} - {edge.description}")
    return "\n".join(lines)
