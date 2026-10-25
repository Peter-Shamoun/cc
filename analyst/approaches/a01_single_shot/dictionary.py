"""Data dictionary for A01, generated from the session and the semantic model.

Column names and types come from DESCRIBE of the sem.* relations in the open session.
Grain, the metric compiler's default filter, and dimension descriptions come from
``claude/schema/03_semantic_model.yaml``. Sample values are not read. The view SQL text
is not copied in: a one-line provenance note points at the file that defines the views.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
SEMANTIC_MODEL_PATH = ROOT / "claude" / "schema" / "03_semantic_model.yaml"
VIEW_SQL_PATH = "claude/schema/02_semantic_layer_views.sql"

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

HEADER = """# Data dictionary
Column names and types are DESCRIBE of the sem.* relations in this session.
Grain, use, and column descriptions are from claude/schema/03_semantic_model.yaml.
The views are defined in claude/schema/02_semantic_layer_views.sql. This dictionary does not include sample values.
A SELECT is not pre-filtered. A default filter listed on a view is applied only by the metric compiler. Write the filter the question's population requires.
"""


def _quote_ident(name: str) -> str:
    if not _IDENT.fullmatch(name):
        raise ValueError(f"relation name {name!r} is not a plain identifier")
    return '"' + name + '"'


def _relation_names(con) -> list[str]:
    rows = con.execute(
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_schema = 'sem' ORDER BY table_name"
    ).fetchall()
    names = []
    for schema, name in rows:
        if not _IDENT.fullmatch(schema) or not _IDENT.fullmatch(name):
            raise ValueError(f"unexpected relation name {schema}.{name}")
        names.append(f"{schema}.{name}")
    return names


def _describe(con, relation: str) -> list[tuple[str, str]]:
    schema, name = relation.split(".")
    quoted = f"{_quote_ident(schema)}.{_quote_ident(name)}"
    rows = con.execute(f"DESCRIBE {quoted}").fetchall()
    return [(row[0], row[1]) for row in rows]


def build_data_dictionary(con, model_path: Path | None = None) -> str:
    """Describe every sem.* relation in ``con``. Raise if the semantic model names a view that is absent."""
    path = Path(model_path) if model_path else SEMANTIC_MODEL_PATH
    model = yaml.safe_load(path.read_text())
    preference = list(model.get("view_preference") or [])
    view_meta = model.get("views") or {}
    descriptions = {}
    for source in (model.get("dimensions") or {}, model.get("time_dimensions") or {}):
        for name, meta in source.items():
            if isinstance(meta, dict) and meta.get("description"):
                descriptions[name] = meta["description"]
    present = _relation_names(con)
    missing = [view for view in preference if view not in present]
    if missing:
        raise RuntimeError(
            "semantic-model views missing from this session: " + ", ".join(missing)
        )
    ordered = [view for view in preference if view in present]
    ordered += sorted(view for view in present if view not in ordered)
    lines = [HEADER.rstrip(), ""]
    for view in ordered:
        meta = view_meta.get(view) or {}
        lines.append(f"## {view}")
        if meta.get("grain"):
            lines.append(f"grain: {meta['grain']}")
        if meta.get("use_for"):
            lines.append(f"use for: {meta['use_for']}")
        if meta.get("default_filter"):
            lines.append(
                f"metric-compiler default filter (not applied to a SELECT): {meta['default_filter']}"
            )
        lines.append(f"defined in: {VIEW_SQL_PATH}")
        lines.append("columns:")
        for column_name, column_type in _describe(con, view):
            description = descriptions.get(column_name)
            if description:
                lines.append(f"- {column_name} {column_type} — {description}")
            else:
                lines.append(f"- {column_name} {column_type}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
