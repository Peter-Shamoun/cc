"""Code-level refusal checks. Prompt wording is not the control that stops a PHI or tenant leak."""
from __future__ import annotations

import re

from .config import CompilerConfig

# Direct identifiers the semantic layer does not expose. Matched on the question, before any SQL.
_PHI = re.compile(
    r"\b(patient names?|names and dates of birth|dates? of birth|\bdob\b|birth dates?|"
    r"member ids?|subscriber ids?|insurance ids?|social security|\bssn\b|"
    r"bank accounts?|home address|phone numbers?)\b",
    re.I,
)
_WRITE = re.compile(
    r"\b(delete|drop|truncate|insert|update|alter|remove)\b.{0,40}\b("
    r"files?|rows?|tables?|data|835|837|claims?|duplicates?)\b|"
    r"\b(delete|drop|truncate)\b",
    re.I,
)

# High-cardinality identifiers that are on sem.* but are not aggregate dimensions.
IDENTIFIER_COLUMNS = frozenset({"claim_key", "check_or_eft_number", "payer_claim_control_number"})


def lexical_refusal(question: str, config: CompilerConfig) -> str | None:
    """Return a refusal reason, or None if the question may proceed to the spec."""
    if not config.phi_lexical:
        return None
    if _PHI.search(question):
        return ("Refused. Patient names, dates of birth, member ids and similar identifiers "
                "are not in the semantic layer and must not be retrieved.")
    if _WRITE.search(question):
        return "Refused. This session is read-only."
    q = question.lower()
    for name in config.other_customer_names:
        if name.lower() in q:
            return (f"Refused. This session can see only {config.session_customer}. "
                    "It cannot answer about another customer.")
    return None
