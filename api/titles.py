"""Server-side conversation titles that can never carry PHI (contract 1.3.0, ``message.end.conversationTitle``).

A question can hold PHI, so the title is never built from the user's text (see ``api.store``). It is built only
from the result's column identifiers ("Paid amount by payer name"), and only when every word in them belongs to a
fixed analytics vocabulary. Column aliases are written by the model and could in theory embed a literal
(``claims_for_jane_doe``), so the check fails closed: one unknown word, a PHI column, or a non-identifier means no
title, and the conversation keeps the generic one.
"""
from __future__ import annotations

import re

from analyzer.tools import is_phi_column

from api.mapping import ACRONYMS

MAX_DIMS = 2
_IDENT = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
_NUMERIC = {"integer", "number", "currency", "percent"}
_ACRONYMS = ACRONYMS | {"ar", "los"}

VOCAB = frozenset("""
    a amount allowed and avg average balance billed by cash category charge charges claim claims class code codes
    name cohort collection collections count cpt current day days denial denials denied department diagnosis dollar
    dollars drg facility first gross icd in last line lines los max mean median min modifier month months net network
    noncurrent number of org organization outstanding paid payer payers payment payments pct per percent place plan
    pos procedure product provider providers quarter rate ratio readmission region revenue service services
    specialty state status submission submissions sum total type value visit visits volume week write year years
    adjustment adjustments ar aging bucket reason group carc rarc npi taxonomy submitted resolved open closed
""".split())


def _words(key: str) -> list[str] | None:
    key = key.strip().lower()
    if not _IDENT.match(key) or is_phi_column(key):
        return None
    words = [w.upper() if w in _ACRONYMS else w for w in key.split("_")]
    return words if all(w.lower() in VOCAB or w.isdigit() for w in words) else None


def dim_words(words: list[str]) -> list[str]:
    """1.10.0 (QA-P6): a dimension reads as its entity: ``payer_name`` -> ``payer``. A lone ``name`` stays."""
    return words[:-1] if len(words) > 1 and words[-1].lower() == "name" else words


def title_from_columns(columns: list[dict]) -> str | None:
    """``columns`` are contract ``Column`` objects. Returns e.g. ``"Denial rate by payer"`` or None."""
    measure, dims = None, []
    for col in columns:
        words = _words(str(col.get("key", "")))
        if words is None or col.get("phi"):
            return None
        if col.get("type") in _NUMERIC and measure is None:
            measure = words
        elif col.get("type") not in _NUMERIC:
            dims.append(" ".join(dim_words(words)))
    if measure is None:
        return None
    head = " ".join(measure)
    if not dims:
        return head[:1].upper() + head[1:]
    title = f"{head} by {' and '.join(dims[:MAX_DIMS])}"
    return title[:1].upper() + title[1:]


def title_from_parts(parts: list[dict]) -> str | None:
    """Title from the first table part of a finished answer, if any."""
    table = next((p for p in parts if p.get("type") == "table"), None)
    return title_from_columns(table.get("columns", [])) if table else None
