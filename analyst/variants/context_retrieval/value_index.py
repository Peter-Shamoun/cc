"""Local value index: payer names, CPT/HCPCS, CARC, RARC, and diagnosis codes.

Built from the logged-in tenant's ``sem.*`` views with fixed aggregate SQL.
The index stores codes, payer names, public code descriptions, and row counts.
It does not store claim keys or other identifiers, and a lookup refuses to run
against a different tenant than the one the index was built for.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher

import sqlglot
from sqlglot import exp

from analyst.variants.context_retrieval.phi import (
    ENTRY_FIELDS,
    QUERY_COLUMNS,
    PhiError,
    assert_columns_allowed,
    assert_entry_fields,
    scrub_description,
)

BUILD_QUERIES: dict[str, str] = {
    "payers": """
        SELECT payer_name, payer_class, names_as_sent, claim_submissions, payments
        FROM sem.payers
        WHERE payer_name IS NOT NULL
    """,
    "procedure_codes": """
        SELECT procedure_code, count(*) AS row_count, 'sem.claim_lines' AS source_view
        FROM sem.claim_lines
        WHERE procedure_code IS NOT NULL
        GROUP BY procedure_code
        UNION ALL
        SELECT procedure_code, count(*) AS row_count, 'sem.remit_lines' AS source_view
        FROM sem.remit_lines
        WHERE procedure_code IS NOT NULL
        GROUP BY procedure_code
    """,
    "carc": """
        SELECT carc_code, min(carc_description) AS carc_description,
               min(carc_category) AS carc_category, count(*) AS row_count
        FROM sem.adjustments
        WHERE carc_code IS NOT NULL
        GROUP BY carc_code
    """,
    "rarc": """
        SELECT remark_code, min(remark_description) AS remark_description, count(*) AS row_count
        FROM sem.remarks
        WHERE remark_code IS NOT NULL
        GROUP BY remark_code
    """,
    "diagnosis_codes": """
        SELECT diagnosis_code, count(*) AS row_count
        FROM sem.diagnoses
        WHERE diagnosis_code IS NOT NULL
        GROUP BY diagnosis_code
    """,
}

KIND_OF_QUERY = {
    "payers": "payer_name",
    "procedure_codes": "procedure_code",
    "carc": "carc",
    "rarc": "rarc",
    "diagnosis_codes": "diagnosis_code",
}

_CODE_KINDS = frozenset({"procedure_code", "carc", "rarc", "diagnosis_code"})


class ValueIndexError(ValueError):
    pass


class TenantScopeError(ValueIndexError):
    """Lookup tenant does not match the tenant whose views were indexed."""


def _columns_read(sql: str) -> set[str]:
    tree = sqlglot.parse_one(sql, read="duckdb")
    if not isinstance(tree, (exp.Select, exp.Union)):
        raise PhiError("value-index SQL must be a single SELECT")
    return {column.name.lower() for column in tree.find_all(exp.Column)}


def assert_query_safe(query_name: str, sql: str) -> None:
    if query_name not in QUERY_COLUMNS:
        raise PhiError(f"unknown value-index query {query_name!r}")
    if re.search(r"(?i)\bselect\s+\*", sql):
        raise PhiError(f"query {query_name} must not use SELECT *")
    statements = [statement for statement in sqlglot.parse(sql, read="duckdb") if statement is not None]
    if len(statements) != 1:
        raise PhiError(f"query {query_name} must be exactly one statement")
    tables = {f"{table.db}.{table.name}".lower() for table in statements[0].find_all(exp.Table) if table.db}
    if not tables or any(not name.startswith("sem.") for name in tables):
        raise PhiError(f"query {query_name} may read only sem.* views, saw {sorted(tables)}")
    assert_columns_allowed(query_name, _columns_read(sql))


def _norm_text(value: str) -> str:
    collapsed = re.sub(r"[^A-Za-z0-9]+", " ", value.upper()).strip()
    return re.sub(r"\s+", " ", collapsed)


def _norm_code(kind: str, value: str) -> str:
    code = re.sub(r"[^A-Za-z0-9]", "", value.upper())
    if kind == "carc":
        return code.lstrip("0") or "0"
    return code


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


@dataclass
class ValueEntry:
    kind: str
    value: str
    description: str | None = None
    category: str | None = None
    payer_class: str | None = None
    row_count: int = 0
    aliases: list[str] = field(default_factory=list)
    source_views: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        payload = {
            "kind": self.kind,
            "value": self.value,
            "description": self.description,
            "category": self.category,
            "payer_class": self.payer_class,
            "row_count": self.row_count,
            "aliases": list(self.aliases),
            "source_views": list(self.source_views),
        }
        assert_entry_fields(payload)
        return payload


@dataclass
class ValueIndex:
    tenant_id: str
    entries: list[ValueEntry]

    def __post_init__(self) -> None:
        if not self.tenant_id or not str(self.tenant_id).strip():
            raise ValueIndexError("tenant_id is required; the index is scoped to one logged-in customer")

    @classmethod
    def build(cls, connection, tenant_id: str, queries: dict[str, str] | None = None) -> "ValueIndex":
        chosen = queries or BUILD_QUERIES
        for name, sql in chosen.items():
            assert_query_safe(name, sql)
        entries: list[ValueEntry] = []
        entries.extend(_payer_entries(connection.execute(chosen["payers"]).fetchall()))
        entries.extend(_procedure_entries(connection.execute(chosen["procedure_codes"]).fetchall()))
        entries.extend(_carc_entries(connection.execute(chosen["carc"]).fetchall()))
        entries.extend(_rarc_entries(connection.execute(chosen["rarc"]).fetchall()))
        entries.extend(_diagnosis_entries(connection.execute(chosen["diagnosis_codes"]).fetchall()))
        entries.sort(key=lambda entry: (entry.kind, entry.value))
        return cls(tenant_id=tenant_id, entries=entries)

    def to_json(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "version": 1,
            "entries": [entry.as_dict() for entry in self.entries],
        }

    def dumps(self) -> str:
        return json.dumps(self.to_json(), indent=1, sort_keys=True)

    @classmethod
    def from_json(cls, payload: dict) -> "ValueIndex":
        if payload.get("version") != 1:
            raise ValueIndexError("unsupported value-index version")
        entries = []
        for raw in payload["entries"]:
            assert_entry_fields(raw)
            entries.append(ValueEntry(**raw))
        return cls(tenant_id=payload["tenant_id"], entries=entries)

    def require_tenant(self, tenant_id: str) -> None:
        if tenant_id != self.tenant_id:
            raise TenantScopeError(
                f"value index was built for tenant {self.tenant_id!r} and cannot be queried as {tenant_id!r}"
            )

    def lookup(self, tenant_id: str, kind: str, text: str, limit: int = 5) -> dict:
        self.require_tenant(tenant_id)
        if kind not in KIND_OF_QUERY.values():
            raise ValueIndexError(f"unknown value kind {kind!r}; use one of {sorted(set(KIND_OF_QUERY.values()))}")
        if not str(text).strip():
            raise ValueIndexError("lookup text is empty")
        scored = []
        for entry in self.entries:
            if entry.kind != kind:
                continue
            score = _score(kind, text, entry)
            if score >= 0.72:
                scored.append((score, entry))
        scored.sort(key=lambda item: (-item[0], -item[1].row_count, item[1].value))
        top = scored[: max(1, min(limit, 20))]
        bound = _is_bound(top)
        return {
            "tenant_id": self.tenant_id,
            "kind": kind,
            "query": text,
            "bound": bound,
            "matches": [_match_dict(score, entry) for score, entry in top],
        }


def _match_dict(score: float, entry: ValueEntry) -> dict:
    return {
        "value": entry.value,
        "score": round(score, 3),
        "row_count": entry.row_count,
        "description": entry.description,
        "category": entry.category,
        "payer_class": entry.payer_class,
        "aliases": entry.aliases[:5],
        "source_views": entry.source_views,
    }


def _is_bound(top: list[tuple[float, ValueEntry]]) -> bool:
    if not top:
        return False
    best = top[0][0]
    second = top[1][0] if len(top) > 1 else 0.0
    if best >= 0.99 and sum(score >= 0.99 for score, _ in top) == 1:
        return True
    return best >= 0.90 and best - second >= 0.08


def _score(kind: str, text: str, entry: ValueEntry) -> float:
    if kind in _CODE_KINDS:
        wanted = _norm_code(kind, text)
        if wanted and wanted == _norm_code(kind, entry.value):
            return 1.0
        if entry.description:
            return _text_score(text, entry.description)
        return 0.0
    return max(_text_score(text, entry.value), *(_text_score(text, alias) for alias in entry.aliases), 0.0)


def _text_score(query: str, candidate: str) -> float:
    left, right = _norm_text(query), _norm_text(candidate)
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    left_tokens, right_tokens = set(left.split()), set(right.split())
    if len(left) >= 4 and left_tokens and left_tokens <= right_tokens:
        return 0.95
    if len(left) >= 4 and (right.startswith(left) or left.startswith(right)):
        return 0.88
    if len(left) >= 6 and left in right:
        return 0.86
    return SequenceMatcher(None, left, right).ratio() * 0.8


def _payer_entries(rows) -> list[ValueEntry]:
    merged: dict[str, ValueEntry] = {}
    for payer_name, payer_class, names_as_sent, claim_submissions, payments in rows:
        aliases = []
        for alias in _as_list(names_as_sent):
            cleaned = scrub_description(alias)
            if cleaned and _norm_text(cleaned) != _norm_text(str(payer_name)):
                aliases.append(cleaned)
        key = str(payer_name)
        current = merged.get(key)
        row_count = int(claim_submissions or 0) + int(payments or 0)
        if current is None:
            merged[key] = ValueEntry(
                "payer_name", key, payer_class=None if payer_class is None else str(payer_class),
                row_count=row_count, aliases=sorted(set(aliases)), source_views=["sem.payers"],
            )
        else:
            current.row_count += row_count
            current.aliases = sorted(set(current.aliases) | set(aliases))
    return list(merged.values())


def _procedure_entries(rows) -> list[ValueEntry]:
    merged: dict[str, ValueEntry] = {}
    for procedure_code, row_count, source_view in rows:
        key = str(procedure_code).upper()
        current = merged.get(key)
        if current is None:
            merged[key] = ValueEntry(
                "procedure_code", key, row_count=int(row_count), source_views=[str(source_view)],
            )
        else:
            current.row_count += int(row_count)
            if str(source_view) not in current.source_views:
                current.source_views.append(str(source_view))
    return list(merged.values())


def _carc_entries(rows) -> list[ValueEntry]:
    return [
        ValueEntry(
            "carc", str(code), description=scrub_description(description),
            category=scrub_description(category), row_count=int(row_count),
            source_views=["sem.adjustments"],
        )
        for code, description, category, row_count in rows
    ]


def _rarc_entries(rows) -> list[ValueEntry]:
    return [
        ValueEntry(
            "rarc", str(code).upper(), description=scrub_description(description),
            row_count=int(row_count), source_views=["sem.remarks"],
        )
        for code, description, row_count in rows
    ]


def _diagnosis_entries(rows) -> list[ValueEntry]:
    return [
        ValueEntry(
            "diagnosis_code", re.sub(r"[^A-Z0-9]", "", str(code).upper()),
            row_count=int(row_count), source_views=["sem.diagnoses"],
        )
        for code, row_count in rows
    ]


_CPT = re.compile(r"\b(\d{5}|\d{4}[A-Z0-9])\b", re.IGNORECASE)
_HCPCS = re.compile(r"\b([A-Z]\d{4})\b", re.IGNORECASE)
_CARC = re.compile(r"\b(?:CARC|reason code)\s*#?\s*([A-Z]?\d{1,3})\b", re.IGNORECASE)
_RARC = re.compile(r"\b((?:MA|N|M)\d{1,4})\b")
_ICD = re.compile(r"\b([A-TV-Z][0-9][0-9A-Z](?:\.[0-9A-Z]{1,4})?)\b")
_MODIFIER = re.compile(r"\bmodifier\s+([A-Z0-9]{2})\b", re.IGNORECASE)
# Words that show up in ordinary questions and also inside payer legal names
# ("Hollister Claims Administration"). A mention made only of these words is not a payer.
_GENERIC = frozenset({
    "payer", "payers", "code", "codes", "claim", "claims", "denial", "denials", "denied",
    "rate", "primary", "current", "adjudication", "adjudications", "total", "billed",
    "submitted", "payment", "payments", "reason", "group", "line", "lines", "amount",
})


def _code_phrase(entry: ValueEntry, question_tokens: set[str], normalized_question: str) -> str | None:
    """The description or category, when every distinctive word of it is in the question.

    One shared word such as 'adjustments' or 'submission' is not enough: long X12
    sentences contain those words without being what the question asked for.
    """
    for label in (entry.description, entry.category):
        text = _norm_text(label or "")
        if not text:
            continue
        if len(text) >= 8 and text in normalized_question:
            return text
        distinctive = [token for token in text.split() if len(token) >= 6 and token.lower() not in _GENERIC]
        if distinctive and all(token in question_tokens for token in distinctive):
            return text
    return None


def resolve_question(index: ValueIndex, tenant_id: str, question: str) -> dict:
    """Map literals and phrases in the question onto this tenant's index. No SQL, no other tenant."""
    index.require_tenant(tenant_id)
    bound, ambiguous, unmatched = [], [], []

    def add(kind: str, text: str, why: str, require_match: bool = False) -> None:
        if _norm_text(text).lower() in _GENERIC or set(_norm_text(text).lower().split()) <= _GENERIC:
            return
        result = index.lookup(tenant_id, kind, text)
        if require_match and (not result["matches"] or result["matches"][0]["score"] < 0.85):
            return
        payload = {"mention": text, "why": why, **result}
        if not result["matches"]:
            unmatched.append(payload)
        elif result["bound"]:
            bound.append(payload)
        else:
            ambiguous.append(payload)

    for match in _CARC.finditer(question):
        add("carc", match.group(1), "carc token")
    for match in _RARC.finditer(question):
        add("rarc", match.group(1), "rarc token")
    for match in _CPT.finditer(question):
        add("procedure_code", match.group(1), "procedure token")
    for match in _HCPCS.finditer(question):
        add("procedure_code", match.group(1), "hcpcs token")
    for match in _ICD.finditer(question):
        add("diagnosis_code", match.group(1), "diagnosis token")
    for match in _MODIFIER.finditer(question):
        unmatched.append({
            "mention": match.group(1), "why": "modifier is not in the value index",
            "kind": "modifier", "bound": False, "matches": [], "tenant_id": tenant_id, "query": match.group(1),
        })

    normalized_question = _norm_text(question)
    question_tokens = set(normalized_question.split())
    phrase_hits: list[ValueEntry] = []
    for entry in index.entries:
        if entry.kind == "payer_name":
            names = [entry.value, *entry.aliases]
            if any(len(_norm_text(name)) >= 4 and _norm_text(name) in normalized_question for name in names):
                add("payer_name", entry.value, "payer name occurs in the question")
            else:
                overlap = []
                for token in _norm_text(entry.value).split():
                    if len(token) >= 5 and token in question_tokens and token.lower() not in _GENERIC and token not in overlap:
                        overlap.append(token)
                if overlap:
                    add("payer_name", " ".join(overlap), "payer token occurs in the question")
        elif entry.kind in {"carc", "rarc"}:
            phrase = _code_phrase(entry, question_tokens, normalized_question)
            if phrase:
                phrase_hits.append(entry)
    for entry in phrase_hits:
        phrase = _code_phrase(entry, question_tokens, normalized_question)
        siblings = [other for other in phrase_hits if other.kind == entry.kind and _code_phrase(other, question_tokens, normalized_question) == phrase]
        if len(siblings) == 1:
            add(entry.kind, entry.value, "code description occurs in the question")
        elif siblings[0] is entry:
            ambiguous.append({
                "mention": phrase, "why": "several codes share this description",
                "tenant_id": tenant_id, "kind": entry.kind, "query": phrase, "bound": False,
                "matches": [_match_dict(0.9, other) for other in siblings],
            })
    for token in normalized_question.split():
        if len(token) < 5 or token.lower() in _GENERIC:
            continue
        speculative = index.lookup(tenant_id, "payer_name", token)
        if speculative["matches"] and speculative["matches"][0]["score"] >= 0.85:
            add("payer_name", token, "payer token in the question", require_match=True)

    def dedupe(items: list[dict]) -> list[dict]:
        seen = set()
        kept = []
        for item in items:
            resolved = item["matches"][0]["value"] if item.get("matches") else item.get("query")
            key = (item.get("kind"), resolved)
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
        return kept

    return {"bound": dedupe(bound), "ambiguous": dedupe(ambiguous), "unmatched": dedupe(unmatched)}
