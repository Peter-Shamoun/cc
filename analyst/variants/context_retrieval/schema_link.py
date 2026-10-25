"""Recall-first schema linking over the ten ``sem.*`` views.

``full`` returns every view and column. ``linked_recall`` may drop a view that
the question gives no evidence for, and falls back to the full schema when fewer
than ``recall_floor_views`` views match. It does not drop columns. ``linked_columns``
also drops columns; that mode exists to measure the failure Maamari et al. report,
not as the recommended prompt.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[3]
SEMANTIC_MODEL_PATH = ROOT / "claude" / "schema" / "03_semantic_model.yaml"

# Question phrases that are enough, on their own, to keep a view. Extra views are
# acceptable. A missing view is the failure this linker is written to avoid.
INTENT_VIEWS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("denial", "denied", "deny", "denials"), ("sem.remit_claims", "sem.adjustments")),
    (("carc", "reason code", "adjustment reason", "contractual", "write-off", "write off",
      "deductible", "copay", "coinsurance"), ("sem.adjustments",)),
    (("rarc", "remark"), ("sem.remarks",)),
    (("cpt", "hcpcs", "procedure", "modifier"), ("sem.claim_lines", "sem.remit_lines")),
    (("diagnosis", "icd"), ("sem.diagnoses", "sem.claims")),
    (("interest", "plb", "recoup", "provider-level", "provider level", "provider adjustment"),
     ("sem.provider_adjustments",)),
    (("unbalanced", "check", "eft"), ("sem.payments",)),
    (("submitted", "submission", "billed", "resubmit", "void", "frequency"), ("sem.claims",)),
    (("payer", "payers"), ("sem.payers", "sem.remit_claims", "sem.claims")),
    (("line", "lines"), ("sem.claim_lines", "sem.remit_lines")),
    (("835", "remit", "remittance", "adjudication", "paid"), ("sem.remit_claims",)),
    (("837",), ("sem.claims", "sem.claim_lines")),
)

# Columns kept even when column pruning is on, because dropping a grain or a
# measure is how linking loses accuracy on a schema that already fits.
GRAIN_COLUMNS = frozenset({
    "claim_key", "payer_key", "payer_name", "payer_class", "is_current_adjudication",
    "is_latest_submission", "is_denied", "is_denied_recorded", "is_denied_signal_v3",
    "is_reversal", "paid_amount", "charge_amount", "line_paid_amount", "line_charge_amount",
    "line_is_denied", "payment_date", "payment_month", "service_date", "service_month",
    "service_date_from", "submission_date", "submission_month", "payer_responsibility",
    "payer_sequence", "procedure_code", "carc_code", "carc_description", "remark_code",
    "diagnosis_code", "principal_diagnosis_code", "group_code", "frequency_code", "frequency",
    "patient_responsibility_amount", "adjustment_amount", "provider_adjustment_amount",
    "plb_reason_code", "has_remit", "claim_status",
})

_TOKEN = re.compile(r"[a-z0-9]+")


class SchemaLinkError(ValueError):
    pass


@dataclass
class LinkedColumn:
    name: str
    data_type: str
    kept: bool


@dataclass
class LinkedView:
    name: str
    score: float
    grain: str
    columns: list[LinkedColumn] = field(default_factory=list)

    @property
    def kept_column_names(self) -> list[str]:
        return [column.name for column in self.columns if column.kept]


@dataclass
class SchemaLink:
    mode: str
    fallback_full: bool
    views: list[LinkedView]
    dropped_views: list[str]

    def kept_relations(self) -> set[str]:
        return {view.name for view in self.views}

    def kept_columns(self) -> set[str]:
        return {name for view in self.views for name in view.kept_column_names}


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def load_view_notes(path: Path | None = None) -> dict[str, str]:
    model = yaml.safe_load((path or SEMANTIC_MODEL_PATH).read_text())
    notes = {}
    for name, meta in model.get("views", {}).items():
        parts = [meta.get("grain") or "", meta.get("use_for") or ""]
        notes[name] = " ".join(part for part in parts if part)
    return notes


def load_column_descriptions(path: Path | None = None) -> dict[str, str]:
    model = yaml.safe_load((path or SEMANTIC_MODEL_PATH).read_text())
    descriptions = {}
    for section in ("dimensions", "time_dimensions"):
        for name, meta in model.get(section, {}).items():
            descriptions[name] = meta.get("description") or ""
    return descriptions


def load_catalog(connection) -> list[tuple[str, list[tuple[str, str]]]]:
    """View name plus (column, type) from the session connection. Server-side, not model SQL."""
    rows = connection.execute(
        """
        SELECT table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'sem'
        ORDER BY table_name, ordinal_position
        """
    ).fetchall()
    if not rows:
        raise SchemaLinkError("the session exposes no sem.* columns; schema linking has nothing to rank")
    by_view: dict[str, list[tuple[str, str]]] = {}
    for table_name, column_name, data_type in rows:
        by_view.setdefault(f"sem.{table_name}", []).append((column_name, data_type))
    return list(by_view.items())


def _intent_hits(question: str) -> dict[str, float]:
    lowered = question.lower()
    scores: dict[str, float] = {}
    for phrases, views in INTENT_VIEWS:
        if any(phrase in lowered for phrase in phrases):
            for view in views:
                scores[view] = scores.get(view, 0.0) + 2.0
    return scores


def link_schema(connection, question: str, *, mode: str = "full", recall_floor_views: int = 2,
                view_notes: dict[str, str] | None = None,
                column_descriptions: dict[str, str] | None = None,
                force_views: set[str] | None = None) -> SchemaLink:
    if mode not in {"full", "linked_recall", "linked_columns"}:
        raise SchemaLinkError(f"unknown schema_mode {mode!r}")
    if recall_floor_views < 1:
        raise SchemaLinkError("recall_floor_views must be at least 1")
    notes = view_notes if view_notes is not None else load_view_notes()
    descriptions = column_descriptions if column_descriptions is not None else load_column_descriptions()
    question_tokens = _tokens(question)
    intent = _intent_hits(question)
    forced = force_views or set()
    ranked: list[LinkedView] = []
    for view_name, columns in load_catalog(connection):
        score = intent.get(view_name, 0.0)
        score += 1.5 * len(question_tokens & _tokens(view_name))
        score += len(question_tokens & _tokens(notes.get(view_name, "")))
        if view_name in forced:
            score += 3.0
        linked_columns: list[LinkedColumn] = []
        for column_name, data_type in columns:
            column_tokens = _tokens(column_name) | _tokens(descriptions.get(column_name, ""))
            column_score = len(question_tokens & column_tokens)
            if column_score:
                score += 0.25
            keep_column = mode != "linked_columns" or column_name in GRAIN_COLUMNS or column_score > 0
            linked_columns.append(LinkedColumn(column_name, data_type, keep_column))
        ranked.append(LinkedView(view_name, score, notes.get(view_name, ""), linked_columns))

    if mode == "full":
        return SchemaLink(mode, False, ranked, [])

    kept = [view for view in ranked if view.score > 0]
    fallback = len(kept) < recall_floor_views
    if fallback:
        for view in ranked:
            for column in view.columns:
                column.kept = True
        return SchemaLink(mode, True, ranked, [])
    dropped = [view.name for view in ranked if view.score <= 0]
    return SchemaLink(mode, False, kept, dropped)


def relations_and_columns(sql: str) -> tuple[set[str], set[str]]:
    """Relations and column names a gold query uses, for a recall check after answering."""
    tree = sqlglot.parse_one(sql, read="duckdb")
    relations = set()
    for table in tree.find_all(exp.Table):
        if table.db and isinstance(table.this, exp.Identifier):
            relations.add(f"{table.db}.{table.name}".lower())
    columns = {column.name.lower() for column in tree.find_all(exp.Column)}
    return relations, columns


def recall_against_sql(link: SchemaLink, sql: str, catalog_columns: set[str] | None = None) -> dict:
    """Which relations and columns of ``sql`` the link dropped.

    CTE aliases are not columns of ``sem.*``. When ``catalog_columns`` is the full
    catalog, only names that exist on a view count as misses.
    """
    relations, columns = relations_and_columns(sql)
    kept_relations = {name.lower() for name in link.kept_relations()}
    kept_columns = {name.lower() for name in link.kept_columns()}
    missing_columns = columns - kept_columns
    if catalog_columns is not None:
        missing_columns &= {name.lower() for name in catalog_columns}
    return {
        "missing_relations": sorted(relations - kept_relations),
        "missing_columns": sorted(missing_columns),
        "fallback_full": link.fallback_full,
        "dropped_views": list(link.dropped_views),
    }
