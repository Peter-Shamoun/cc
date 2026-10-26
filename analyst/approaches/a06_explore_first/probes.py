"""Cheap probe SQL and the notes those probes compress into.

Each probe is one SELECT. Summary and enum histograms share a scan. A code or a
name is listed only when the summary already showed it has at most
DISTINCT_LIST_LIMIT distinct values. Join probes aggregate each side by key
before they join, so a fan-out is a number in the notes and not a row explosion.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal

from .catalog import (
    FOLLOWUP_VALUE_ROLES,
    HISTOGRAM_ROLES,
    JOINS,
    NEVER_LIST_ROLES,
    RANGE_ROLES,
    VIEW_BY_NAME,
    JoinEdge,
)

MAX_VIEWS = 4
MAX_COLUMNS_PER_VIEW = 8
MAX_JOINS = 3
MAX_LITERALS = 2
DISTINCT_LIST_LIMIT = 12
VALUE_DISPLAY_LIMIT = 12
LITERAL_MAX_LENGTH = 80

NOTES_HEADER = "Exploration notes (unfiltered; probes ignore the question's filters):"


def maximum_probe_statements() -> int:
    """Hard ceiling: summary plus one value follow-up per view, then joins and literals."""
    return MAX_VIEWS * 2 + MAX_JOINS + MAX_LITERALS


def quote_ident(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError(f"name {name!r} is not a plain identifier")
    return f'"{name}"'


def quote_relation(view_name: str) -> str:
    schema, dot, table = view_name.partition(".")
    if not dot or schema != "sem" or view_name not in VIEW_BY_NAME:
        raise ValueError(f"relation {view_name!r} is not a sem view in the catalog")
    return f"{quote_ident(schema)}.{quote_ident(table)}"


def like_pattern(text: str) -> str:
    """A contains-pattern that treats % and _ in the question as ordinary characters."""
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_").replace("'", "''")
    return f"'%{escaped}%'"


@dataclass(frozen=True)
class LiteralRequest:
    view: str
    column: str
    text: str


@dataclass
class ExplorationPlan:
    views: list[str]
    columns: dict[str, list[str]]
    literals: list[LiteralRequest]
    refusal_reason: str | None
    dropped: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "views": list(self.views),
            "columns": {view: list(names) for view, names in self.columns.items()},
            "literals": [{"view": item.view, "column": item.column, "text": item.text} for item in self.literals],
            "refusal_reason": self.refusal_reason,
            "dropped": list(self.dropped),
        }


@dataclass(frozen=True)
class Probe:
    kind: str
    sql: str
    view: str
    columns: tuple[str, ...] = ()
    join_left: str = ""
    join_right: str = ""
    join_description: str = ""
    literal_column: str = ""
    literal_text: str = ""


@dataclass
class ProbeExecution:
    probe: Probe
    columns: list[str]
    rows: list[tuple]
    error: str | None


def selected_joins(view_names: list[str]) -> list[JoinEdge]:
    selected = set(view_names)
    return [edge for edge in JOINS if edge.left_view in selected and edge.right_view in selected][:MAX_JOINS]


def build_summary_probe(view_name: str, column_names: list[str]) -> Probe:
    view = VIEW_BY_NAME[view_name]
    select_parts = ["count(*) AS n_rows"]
    resolved = []
    for name in column_names:
        column = view.column(name)
        if column is None:
            raise ValueError(f"{view_name}.{name} is not in the catalog")
        resolved.append(column)
        ident = quote_ident(column.name)
        select_parts.append(
            f"count(*) FILTER (WHERE {ident} IS NULL) AS {quote_ident(column.name + '__nulls')}")
        if column.role != "list":
            select_parts.append(
                f"count(DISTINCT {ident}) AS {quote_ident(column.name + '__distinct')}")
        if column.role in RANGE_ROLES:
            select_parts.append(f"min({ident}) AS {quote_ident(column.name + '__min')}")
            select_parts.append(f"max({ident}) AS {quote_ident(column.name + '__max')}")
        if column.role in HISTOGRAM_ROLES:
            select_parts.append(
                f"histogram(CAST({ident} AS VARCHAR)) AS {quote_ident(column.name + '__hist')}")
    sql = "SELECT\n  " + ",\n  ".join(select_parts) + f"\nFROM {quote_relation(view_name)}"
    return Probe("summary", sql, view_name, tuple(column.name for column in resolved))


def followup_column_names(view_name: str, column_names: list[str], summary: ProbeExecution) -> list[str]:
    """Code and text columns whose distinct count is small enough to list exactly."""
    if summary.error:
        return []
    row = _row(summary)
    if row is None:
        return []
    chosen = []
    view = VIEW_BY_NAME[view_name]
    for name in column_names:
        column = view.column(name)
        if column is None or column.role not in FOLLOWUP_VALUE_ROLES:
            continue
        distinct = row.get(name + "__distinct")
        if distinct is not None and 1 <= int(distinct) <= DISTINCT_LIST_LIMIT:
            chosen.append(name)
    return chosen


def build_values_probe(view_name: str, column_names: list[str]) -> Probe:
    parts = []
    for name in column_names:
        ident = quote_ident(name)
        parts.append(f"histogram(CAST({ident} AS VARCHAR)) AS {quote_ident(name + '__hist')}")
    sql = "SELECT " + ", ".join(parts) + f" FROM {quote_relation(view_name)}"
    return Probe("values", sql, view_name, tuple(column_names))


def build_join_probe(edge: JoinEdge) -> Probe:
    left_column = quote_ident(edge.left_column)
    right_column = quote_ident(edge.right_column)
    sql = f"""WITH left_keys AS (
  SELECT {left_column} AS join_key, count(*) AS n FROM {quote_relation(edge.left_view)} GROUP BY 1
),
right_keys AS (
  SELECT {right_column} AS join_key, count(*) AS n FROM {quote_relation(edge.right_view)} GROUP BY 1
)
SELECT
  count(l.join_key) AS left_distinct_keys,
  count(r.join_key) AS right_distinct_keys,
  count(*) FILTER (WHERE l.join_key IS NOT NULL AND r.join_key IS NOT NULL) AS matched_keys,
  coalesce(sum(l.n), 0) AS left_rows,
  coalesce(sum(r.n), 0) AS right_rows,
  coalesce(sum(CASE WHEN l.join_key IS NOT NULL AND r.join_key IS NOT NULL THEN l.n * r.n END), 0) AS joined_rows,
  coalesce(max(r.n), 0) AS max_right_rows_per_key
FROM left_keys l
FULL OUTER JOIN right_keys r ON l.join_key = r.join_key"""
    return Probe(
        "join", sql, edge.left_view,
        join_left=f"{edge.left_view}.{edge.left_column}",
        join_right=f"{edge.right_view}.{edge.right_column}",
        join_description=edge.description,
    )


def build_literal_probe(literal: LiteralRequest) -> Probe:
    ident = quote_ident(literal.column)
    sql = f"""SELECT {ident} AS value, count(*) AS n
FROM {quote_relation(literal.view)}
WHERE lower(CAST({ident} AS VARCHAR)) LIKE {like_pattern(literal.text)} ESCAPE '\\'
GROUP BY 1
ORDER BY n DESC, value
LIMIT {VALUE_DISPLAY_LIMIT}"""
    return Probe("literal", sql, literal.view, (literal.column,),
                 literal_column=literal.column, literal_text=literal.text)


def compress(plan: ExplorationPlan, executions: list[ProbeExecution]) -> str:
    summaries = {item.probe.view: item for item in executions if item.probe.kind == "summary"}
    value_probes = {item.probe.view: item for item in executions if item.probe.kind == "values"}
    lines = [NOTES_HEADER]
    if not plan.views:
        lines.append("No columns were probed.")
    for view_name in plan.views:
        summary = summaries.get(view_name)
        if summary is None or summary.error or _row(summary) is None:
            reason = summary.error if summary and summary.error else "no row"
            lines.append(f"{view_name}: probe failed: {reason}")
            continue
        row = _row(summary)
        row_count = int(row["n_rows"])
        lines.append(f"{view_name}: {row_count} rows")
        if row_count == 0:
            continue
        histograms = _histograms(summary)
        value_probe = value_probes.get(view_name)
        value_error = value_probe.error if value_probe else None
        if value_probe and not value_error:
            histograms.update(_histograms(value_probe))
        view = VIEW_BY_NAME[view_name]
        for name in plan.columns[view_name]:
            lines.append("  " + _column_note(view.column(name), row, row_count, histograms, value_error))
    for item in executions:
        if item.probe.kind == "join":
            lines.append(_join_note(item))
        elif item.probe.kind == "literal":
            lines.append(_literal_note(item))
    return "\n".join(lines)


def _column_note(column, row: dict, row_count: int, histograms: dict, value_error: str | None) -> str:
    null_count = int(row[column.name + "__nulls"])
    pieces = [f"null {null_count}/{row_count} ({_percent(null_count, row_count)})"]
    distinct_count = None
    if column.role != "list":
        distinct_count = int(row[column.name + "__distinct"])
        pieces.append(f"{distinct_count} distinct")
    if column.role == "date" and null_count < row_count:
        pieces.append(
            f"range {_format_scalar(row.get(column.name + '__min'))} .. {_format_scalar(row.get(column.name + '__max'))}")
    if column.role == "measure" and null_count < row_count:
        pieces.append(f"min {_format_scalar(row.get(column.name + '__min'))}")
        pieces.append(f"max {_format_scalar(row.get(column.name + '__max'))}")
    if column.role in NEVER_LIST_ROLES:
        pieces.append("values not listed")
    elif column.name in histograms:
        rendered = _render_histogram(histograms[column.name])
        if rendered:
            pieces.append(rendered)
    elif column.role in FOLLOWUP_VALUE_ROLES and distinct_count is not None and distinct_count > DISTINCT_LIST_LIMIT:
        pieces.append("values not listed")
    elif (column.role in FOLLOWUP_VALUE_ROLES and value_error and distinct_count is not None
          and 1 <= distinct_count <= DISTINCT_LIST_LIMIT):
        pieces.append(f"values not listed (probe failed: {value_error})")
    return f"{column.name}: " + "; ".join(pieces)


def _render_histogram(value) -> str:
    items = _histogram_items(value)[:VALUE_DISPLAY_LIMIT]
    all_items = _histogram_items(value)
    if not items:
        return ""
    rendered = ", ".join(f"{name} x{count}" for name, count in items)
    hidden = len(all_items) - len(items)
    if hidden:
        rendered += f", and {hidden} more"
    return rendered


def _histogram_items(value) -> list[tuple[str, int]]:
    if not value:
        return []
    if isinstance(value, dict):
        pairs = list(value.items())
    else:
        pairs = list(dict(value).items())
    items = [(str(key), int(count)) for key, count in pairs]
    items.sort(key=lambda item: (-item[1], item[0]))
    return items


def _join_note(execution: ProbeExecution) -> str:
    probe = execution.probe
    prefix = f"join {probe.join_left} = {probe.join_right} ({probe.join_description})"
    if execution.error:
        return f"{prefix}: probe failed: {execution.error}"
    row = _row(execution)
    if row is None:
        return f"{prefix}: probe failed: no row"
    left_rows = int(row["left_rows"])
    joined_rows = int(row["joined_rows"])
    if left_rows == 0:
        effect = "no left rows"
    elif joined_rows > left_rows:
        effect = "multiplies left rows"
    else:
        effect = "does not multiply left rows"
    return (
        f"{prefix}: left_keys={int(row['left_distinct_keys'])}, right_keys={int(row['right_distinct_keys'])}, "
        f"matched_keys={int(row['matched_keys'])}, left_rows={left_rows}, right_rows={int(row['right_rows'])}, "
        f"joined_rows={joined_rows}, max_right_rows_per_key={int(row['max_right_rows_per_key'])}; {effect}"
    )


def _literal_note(execution: ProbeExecution) -> str:
    probe = execution.probe
    shown = json.dumps(probe.literal_text, ensure_ascii=True)
    prefix = f"literal {probe.view}.{probe.literal_column} containing {shown}"
    if execution.error:
        return f"{prefix}: probe failed: {execution.error}"
    if not execution.rows:
        return f"{prefix}: no stored value"
    rendered = ", ".join(f"{_format_scalar(row[0])} x{int(row[1])}" for row in execution.rows[:VALUE_DISPLAY_LIMIT])
    return f"{prefix}: {rendered}"


def _row(execution: ProbeExecution) -> dict | None:
    if execution.error or not execution.rows:
        return None
    return dict(zip(execution.columns, execution.rows[0]))


def _histograms(execution: ProbeExecution) -> dict:
    row = _row(execution)
    if row is None:
        return {}
    return {name[: -len("__hist")]: value for name, value in row.items() if name.endswith("__hist")}


def _percent(part: int, whole: int) -> str:
    if whole <= 0:
        return "0%"
    return f"{round(100 * part / whole)}%"


def _format_scalar(value) -> str:
    if value is None:
        return "null"
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, Decimal):
        return f"{float(value):.4g}"
    if isinstance(value, float):
        return f"{value:.4g}"
    return str(value)
