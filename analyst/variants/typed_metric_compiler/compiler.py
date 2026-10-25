"""Validate a QuestionSpec and compile it with the existing semantic compiler.

The SQL for ordinary metrics, joins-that-are-not-joins, grain choice, reversal filters and
ratio arithmetic stays in claude/schema/04_agent_interface.py. This module adds the date-basis
check, the cohort population, the received-date overlay, and the projection the matcher can score.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot
from sqlglot import exp

from .config import CompilerConfig
from .phi import IDENTIFIER_COLUMNS
from .spec import COHORT_BASES, EVENT_BASES, CannotExpress, QuestionSpec, SpecError

ROOT = Path(__file__).resolve().parents[3]
INTERFACE_PATH = ROOT / "claude" / "schema" / "04_agent_interface.py"
MODEL_PATH = ROOT / "claude" / "schema" / "03_semantic_model.yaml"

# Columns a group_by must not use. claim_key is a patient control number; the other two are payment identifiers.
_FORBIDDEN_GROUP = IDENTIFIER_COLUMNS

_EVENT_FIELD = {
    "payment": ("payment_date",),
    "service": ("service_date", "service_date_from"),
    "submission": ("submission_date",),
    "payer_received": ("payer_received_date",),
}
_COHORT_ANCHOR = {
    "cohort_submission": "submission_date",
    "cohort_service": "service_date_from",
}


@dataclass
class CompileResult:
    sql: str
    view: str
    lane: str
    notes: list[str] = field(default_factory=list)
    date_basis: str = "none"


def load_interface():
    name = "cc_agent_interface"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, INTERFACE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def load_api(con, config: CompilerConfig):
    module = load_interface()
    api = module.SemanticAPI(con, str(MODEL_PATH))
    if config.payer_received_basis:
        api.dims["payer_received_date"] = {
            "description": "Date the payer reports it received the claim (DTM*050). Not the file arrival date.",
        }
    return module, api


def _literal(value) -> str:
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if value is None:
        return "NULL"
    return "'" + str(value).replace("'", "''") + "'"


def _inline(sql: str, params: list) -> str:
    for value in params:
        sql = sql.replace("?", _literal(value), 1)
    if "?" in sql:
        raise SpecError("compiled SQL has more placeholders than parameters")
    return sql


def _group_alias(g) -> str:
    if isinstance(g, str):
        return g
    if g.get("grain"):
        return f"{g['field']}_{g['grain']}"
    return g["field"]


def _project(sql: str, spec: QuestionSpec) -> str:
    cols = [_group_alias(g) for g in spec.group_by] + list(spec.metrics)
    quoted = ", ".join(f'"{c}"' for c in cols)
    return f"SELECT {quoted} FROM ({sql}) AS compiled"


def _check_group_by(spec: QuestionSpec, config: CompilerConfig) -> None:
    if not config.block_identifier_groupby:
        return
    for name in spec.group_names():
        if name in _FORBIDDEN_GROUP:
            raise CannotExpress(
                f"{name} identifies a claim or a payment. It cannot be a group_by.",
                code="identifier_group_by",
            )


def _time_range(spec: QuestionSpec, field_name: str) -> dict | None:
    if spec.time_start is None and spec.time_end is None:
        return None
    return {
        "field": field_name,
        "start": spec.time_start or "1900-01-01",
        "end": spec.time_end or "2999-12-31",
    }


def _with_time_group(spec: QuestionSpec, field_name: str) -> list:
    group_by = list(spec.group_by)
    if not spec.time_grain:
        return group_by
    already = False
    for g in group_by:
        name = g if isinstance(g, str) else g.get("field")
        if name == field_name or (isinstance(name, str) and name.startswith(field_name)):
            already = True
    if not already:
        group_by.append({"field": field_name, "grain": spec.time_grain})
    return group_by


def _call_compile(module, api, spec: QuestionSpec, *, time_field: str | None, group_by: list, config: CompilerConfig):
    kwargs = {
        "metrics": [m for m in spec.metrics if m != "cohort_claims"],
        "group_by": group_by,
        "filters": spec.filters,
        "order_by": spec.order_by,
        "limit": spec.limit or config.default_limit,
        "include_superseded": spec.include_superseded,
        # 0 disables the catalog floor. None would apply min_group_size_default (20).
        "min_group_size": config.min_group_size_when_unspecified if spec.min_group_size is None else spec.min_group_size,
    }
    if time_field and (spec.time_start or spec.time_end):
        kwargs["time_range"] = _time_range(spec, time_field)
    try:
        return api.compile(**kwargs)
    except module.SemanticError as e:
        text = str(e)
        if text.startswith("Unknown") or text.startswith("Unsupported") or "order_by field" in text or "filters must" in text:
            raise SpecError(text) from e
        raise CannotExpress(text, code="catalog_view") from e


def _cohort_body_sql(spec: QuestionSpec) -> str:
    anchor = _COHORT_ANCHOR[spec.date_basis]
    start = spec.time_start or "1900-01-01"
    end = spec.time_end or "2999-12-31"
    # HAVING, not WHERE: the population date is the first anchor, not the row's own date.
    return (
        "SELECT claim_key FROM sem.claims\n"
        "GROUP BY claim_key\n"
        f"HAVING min({anchor}) >= DATE '{start}' "
        f"AND min({anchor}) < (DATE '{end}' + INTERVAL 1 DAY)"
    )


def _attach_cohort(sql: str, body: str) -> str:
    """Prefix the compiler's `WITH agg AS` with the cohort CTE and restrict agg to those claim keys.

    The base compiler always emits that exact shape (see SemanticAPI.compile). The cohort body
    has no WHERE, so the first WHERE/GROUP BY below belongs to agg.
    """
    if not sql.startswith("WITH agg AS ("):
        raise CannotExpress("compiled SQL is not the base compiler's WITH agg shape", code="cohort_shape")
    sql = "WITH cohort AS (\n" + body + "\n), " + sql[len("WITH "):]
    start = sql.find("agg AS (")
    predicate = "claim_key IN (SELECT claim_key FROM cohort)"
    where_at = sql.find("\nWHERE ", start)
    group_at = sql.find("\nGROUP BY ", start)
    close_at = sql.find("\n)", start)
    if where_at != -1 and (group_at == -1 or where_at < group_at):
        return sql[:where_at + len("\nWHERE ")] + f"({predicate})\n  AND " + sql[where_at + len("\nWHERE "):]
    insert_at = group_at if group_at != -1 else close_at
    if insert_at == -1:
        raise CannotExpress("could not find the agg body to restrict", code="cohort_shape")
    return sql[:insert_at] + f"\nWHERE {predicate}" + sql[insert_at:]


def _compile_cohort(module, api, spec: QuestionSpec, config: CompilerConfig) -> CompileResult:
    if not config.cohort_compiler:
        raise CannotExpress("cohort compiler is off for this arm", code="cohort_disabled")
    if spec.time_grain:
        raise CannotExpress(
            "grouping by the cohort's own grain is outside the typed fragment",
            code="cohort_grain",
        )
    if not spec.time_start and not spec.time_end:
        raise CannotExpress("a cohort basis needs time_start or time_end", code="cohort_window")
    if spec.metrics == ["cohort_claims"]:
        if spec.filters or spec.group_by:
            raise CannotExpress(
                "cohort_claims counts the population only. A filtered or grouped cohort needs a catalog metric.",
                code="cohort_claims_shape",
            )
        sql = "WITH cohort AS (\n" + _cohort_body_sql(spec) + "\n)\nSELECT count(*) AS cohort_claims FROM cohort"
        return CompileResult(sql=sql, view="sem.claims", lane="typed_cohort", date_basis=spec.date_basis,
                             notes=["Population is one claim_key per first anchor date in the window."])
    if "cohort_claims" in spec.metrics:
        raise CannotExpress("cohort_claims cannot be combined with another metric", code="cohort_claims_shape")
    compiled = _call_compile(module, api, spec, time_field=None, group_by=list(spec.group_by), config=config)
    if "claim_key" not in api.columns.get(compiled.view, {}):
        raise CannotExpress(
            f"{compiled.view} has no claim_key, so a claim cohort cannot restrict it",
            code="cohort_view",
        )
    sql = _inline(compiled.sql, compiled.params)
    sql = _attach_cohort(sql, _cohort_body_sql(spec))
    if config.project_requested_only:
        sql = _project(sql, spec)
    return CompileResult(sql=sql, view=compiled.view, lane="typed_cohort", date_basis=spec.date_basis,
                         notes=list(compiled.notes) + [f"Restricted to the {spec.date_basis} population."])


def _compile_event(module, api, spec: QuestionSpec, config: CompilerConfig) -> CompileResult:
    if spec.date_basis == "payer_received" and not config.payer_received_basis:
        raise CannotExpress("payer_received basis is off for this arm", code="payer_received_disabled")
    fields = _EVENT_FIELD[spec.date_basis] if spec.date_basis in _EVENT_FIELD else ()
    # A basis with no window and no time grain does not add a column. 'none' is that case.
    # An event basis still records which clock the model chose, and adds a column only when
    # the spec filters or groups by it.
    last_error: Exception | None = None
    chosen = None
    group_by = list(spec.group_by)
    if spec.date_basis == "none":
        compiled = _call_compile(module, api, spec, time_field=None, group_by=group_by, config=config)
    else:
        compiled = None
        for field_name in fields:
            group_by = _with_time_group(spec, field_name)
            try:
                compiled = _call_compile(module, api, spec, time_field=field_name, group_by=group_by, config=config)
                chosen = field_name
                break
            except CannotExpress as e:
                last_error = e
                continue
        if compiled is None:
            raise CannotExpress(str(last_error) if last_error else "no time field for this basis", code="date_field")
    sql = _inline(compiled.sql, compiled.params)
    # Projection uses the group_by that compile actually saw (grain alias included).
    projected = QuestionSpec(
        metrics=list(spec.metrics),
        date_basis=spec.date_basis,
        group_by=group_by,
    )
    if config.project_requested_only:
        sql = _project(sql, projected)
    notes = list(compiled.notes)
    if chosen:
        notes.append(f"date_basis={spec.date_basis} field={chosen}")
    return CompileResult(sql=sql, view=compiled.view, lane="typed", date_basis=spec.date_basis, notes=notes)


def compile_spec(module, api, spec: QuestionSpec, config: CompilerConfig) -> CompileResult:
    if spec.intent != "aggregate":
        raise SpecError(f"compile_spec got intent {spec.intent}")
    if config.date_basis_required and spec.date_basis is None:
        raise CannotExpress("date_basis is required", code="ambiguous_date_basis")
    _check_group_by(spec, config)
    if spec.date_basis == "file_received":
        raise CannotExpress(
            "File arrival date (raw received_date) is not a column of any public sem.* view. "
            "Refusing to substitute payment_date.",
            code="file_received",
        )
    if spec.date_basis in COHORT_BASES:
        return _compile_cohort(module, api, spec, config)
    if spec.date_basis not in EVENT_BASES and spec.date_basis != "none":
        raise SpecError(f"unknown date_basis {spec.date_basis}")
    return _compile_event(module, api, spec, config)


def identifier_projected(sql: str) -> str | None:
    """Outer select list must not be a claim or payment identifier. Aggregates over them are fine."""
    try:
        tree = sqlglot.parse_one(sql, read="duckdb")
    except sqlglot.errors.ParseError:
        return None
    for expression in tree.selects:
        if isinstance(expression, exp.Star):
            return "*"
        node = expression.this if isinstance(expression, exp.Alias) else expression
        if isinstance(node, exp.Column) and node.name.lower() in IDENTIFIER_COLUMNS:
            return node.name.lower()
    return None
