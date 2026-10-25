"""Columns and text the value index is allowed to store.

The index is a per-tenant list of codes and payer names with aggregate row counts.
It is not a sample of claim rows. Patient control numbers, member ids, provider
identifiers, check numbers, and free-text that looks like an identifier are rejected.
"""
from __future__ import annotations

import re

# Kinds the resolver may look up. Anything else fails closed.
VALUE_KINDS = ("payer_name", "procedure_code", "carc", "rarc", "diagnosis_code")

# Columns a build query may read. A query that names anything else is rejected
# before it runs. Aggregate counts are not identifiers.
QUERY_COLUMNS: dict[str, frozenset[str]] = {
    "payers": frozenset({"payer_name", "payer_class", "names_as_sent", "claim_submissions", "payments"}),
    "procedure_codes": frozenset({"procedure_code"}),
    "carc": frozenset({"carc_code", "carc_description", "carc_category"}),
    "rarc": frozenset({"remark_code", "remark_description"}),
    "diagnosis_codes": frozenset({"diagnosis_code"}),
}

# Never accepted as an indexed column, even if a caller passes a custom query.
FORBIDDEN_COLUMNS = frozenset({
    "claim_key", "claim_id", "claim_payer_key", "remit_claim_id", "remit_line_id",
    "claim_line_id", "adjustment_id", "remark_id", "diagnosis_id", "payment_id",
    "provider_adjustment_id", "payer_key", "payer_claim_control_number",
    "check_or_eft_number", "line_item_control_number", "reference_id",
    "billing_npi", "rendering_npi", "referring_npi", "billing_provider_name",
    "billing_provider_state", "source_file_id", "file_id", "member_id",
    "subscriber_id", "patient_name", "birth_date", "date_of_birth",
})

FORBIDDEN_KEY_FRAGMENTS = (
    "patient", "member", "subscriber", "birth", "ssn", "address", "phone", "email",
    "npi", "control_number",
)

# Stored descriptions are code-list text (CARC/RARC), not claim notes. Drop the
# description if it contains an identifier pattern; keep the code.
_PHI_TEXT = re.compile(
    r"\b(ssn|social security|date of birth|dob|member id|subscriber id|patient name)\b"
    r"|\b\d{3}-\d{2}-\d{4}\b"
    r"|\b\d{9,}\b",
    re.IGNORECASE,
)

ENTRY_FIELDS = frozenset({
    "kind", "value", "description", "category", "row_count", "aliases", "source_views", "payer_class",
})


class PhiError(ValueError):
    """A query or a stored field would put an identifier into the index."""


def scrub_description(text: str | None) -> str | None:
    if text is None:
        return None
    cleaned = " ".join(str(text).split())
    if not cleaned or _PHI_TEXT.search(cleaned):
        return None
    return cleaned[:200]


def assert_columns_allowed(query_name: str, column_names: set[str]) -> None:
    allowed = QUERY_COLUMNS.get(query_name)
    if allowed is None:
        raise PhiError(f"unknown value-index query {query_name!r}")
    extra = {c.lower() for c in column_names} - allowed
    forbidden = {c.lower() for c in column_names} & {f.lower() for f in FORBIDDEN_COLUMNS}
    if extra or forbidden:
        raise PhiError(
            f"query {query_name} reads columns that are not on its allowlist: "
            f"{sorted(extra | forbidden)}"
        )
    for name in column_names:
        lowered = name.lower()
        if any(fragment in lowered for fragment in FORBIDDEN_KEY_FRAGMENTS):
            raise PhiError(f"query {query_name} reads identifier-like column {name}")


def assert_entry_fields(entry: dict) -> None:
    extra = set(entry) - ENTRY_FIELDS
    if extra:
        raise PhiError(f"value-index entry has fields that are not stored: {sorted(extra)}")
    for key in entry:
        lowered = key.lower()
        if lowered in FORBIDDEN_COLUMNS or any(fragment in lowered for fragment in FORBIDDEN_KEY_FRAGMENTS):
            raise PhiError(f"value-index entry field {key} is not allowed")
