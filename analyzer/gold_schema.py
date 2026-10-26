"""The gold (``sem.*``) schema block of the analyzer's cached system prompt.

Generated, never hand-written: every ``sem.*`` view the session can read, with its grain, default filter and use from
the semantic model (``claude/schema/03_semantic_model.yaml``), then one line per column: name, type (from the view
itself), meaning (the semantic model's dimension description, when it has one) and, for a low-cardinality text column,
its allowed values (the enum profile, sorted). Then how each semantic-model metric is computed on that view. The same
views, model and data give the same bytes, so the block is computed once per customer and gold version and reused;
``lossless.*`` is never described here (it is reachable only through ``escalate``, then ``list`` / ``describe``).
"""
from __future__ import annotations

import threading

from analyzer.tools import _HIGH_CARDINALITY_COLUMNS, _IDENT, ToolLayer, _metric_derivations, is_phi_column

#: a text column with at most this many distinct values lists them all
MAXIMUM_ALLOWED_VALUES = 12

_schema_blocks: dict[tuple[str, str], str] = {}
_coverage_lines: dict[tuple[str, str], str] = {}
_schema_blocks_lock = threading.Lock()
#: (relation, date column, how the user message names it), first one the session has wins
COVERAGE_DATE_COLUMNS = (("sem.remit_claims", "payment_date", "payment date"),
                         ("sem.claims", "service_date", "service date"))


def gold_schema_block(tools: ToolLayer) -> str:
    """The block for the tool layer's session (its customer and gold version), built on first use."""
    key = (str(tools.session.customer), str(tools.session.version))
    with _schema_blocks_lock:
        block = _schema_blocks.get(key)
        if block is None:
            block = _schema_blocks[key] = render_gold_schema(tools)
        return block


def render_gold_schema(tools: ToolLayer) -> str:
    model = tools._semantic_model()
    views = model.get("views", {})
    meanings = {**model.get("dimensions", {}), **model.get("time_dimensions", {})}
    lines = ["Gold schema (every sem.* view you can read; generated from the semantic model and the data):"]
    for relation in sorted(name for name in tools.list_relations(charge=False) if name.startswith("sem.")):
        view = views.get(relation, {})
        header = f"{relation}"
        for label, field in (("grain", "grain"), ("default filter", "default_filter"), ("use for", "use_for")):
            if view.get(field):
                header += f" | {label}: {view[field]}"
        lines.append("")
        lines.append(header)
        for name, column_type, *_rest in tools.session.con.execute(f"DESCRIBE {relation}").fetchall():
            line = f"- {name} {column_type}"
            meaning = (meanings.get(name) or {}).get("description")
            if meaning:
                line += f": {' '.join(str(meaning).split())}"
            values = allowed_values(tools, relation, name, column_type)
            if values:
                line += f" [values: {' | '.join(values)}]"
            lines.append(line)
        for derivation in _metric_derivations(model, relation):
            lines.append(f"  metric {derivation['metric']} = {derivation['expression']}")
    return "\n".join(lines) + "\n"


def allowed_values(tools: ToolLayer, relation: str, column: str, column_type: str) -> list[str] | None:
    """Every non-null value of a low-cardinality text column, sorted; None for any other column. Identifier and
    high-cardinality columns are never read. The query goes through the guard like any tool-authored SQL."""
    if not column_type.startswith("VARCHAR") or is_phi_column(column) or column in _HIGH_CARDINALITY_COLUMNS \
            or not _IDENT.fullmatch(column):
        return None
    rows = tools._guarded_profile(f'SELECT DISTINCT "{column}" FROM {relation} WHERE "{column}" IS NOT NULL '
                                  f"LIMIT {MAXIMUM_ALLOWED_VALUES + 1}").fetchall()
    if len(rows) > MAXIMUM_ALLOWED_VALUES:
        return None
    return sorted(str(row[0]) for row in rows)


def data_coverage_line(tools: ToolLayer) -> str:
    """"This customer's data covers <earliest> to <latest> (by payment date)." for the session's customer and gold
    version, computed once (it goes in the first user message, after the cached prefix)."""
    key = (str(tools.session.customer), str(tools.session.version))
    with _schema_blocks_lock:
        line = _coverage_lines.get(key)
        if line is None:
            line = _coverage_lines[key] = _coverage(tools)
        return line


def _coverage(tools: ToolLayer) -> str:
    readable = set(tools.list_relations(charge=False))
    for relation, column, label in COVERAGE_DATE_COLUMNS:
        if relation in readable:
            earliest, latest = tools._guarded_profile(f"SELECT min({column}), max({column}) FROM {relation}").fetchone()
            if earliest is not None:
                return f"This customer's data covers {earliest} to {latest} (by {label})."
    return "This customer's data has no dated rows."
