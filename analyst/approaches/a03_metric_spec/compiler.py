"""Turn a metric spec into one guarded SELECT over sem.*.

The model names a population, metrics, filters, grain, a time window, and tie and
rounding rules. Joins exist only as catalog links. Anything else is CannotExpress,
which is the only signal that opens a free-SQL fallback.
"""
from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass

from bench.guard import GuardError, guard

from .catalog import (
    CLAIM_LEVEL_MEASURES,
    FIELD_HINTS,
    LINKS,
    POPULATIONS,
    PRIMARY_ATTRIBUTE_FIELDS,
    Population,
    field_kind,
    population_columns,
)
from .compiler_errors import CannotExpress, SpecError
from .schema import AGGS, BUCKETS, FILTER_OPS, Filter, Metric, MetricSpec, Order, parse_spec

_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class CompiledSpec:
    sql: str
    population: str
    link: str | None
    notes: list[str]
    output_columns: list[str]


@dataclass
class Binding:
    exprs: dict[str, str]
    kinds: dict[str, str]
    modifier_columns: tuple[str, ...]

    def resolve(self, name: str) -> str:
        if name not in self.exprs:
            hint = FIELD_HINTS.get(name, "")
            known = ", ".join(sorted(self.exprs))
            raise CannotExpress(f"field {name!r} is not on this population. Fields here: {known}", hint=hint)
        return self.exprs[name]

    def kind(self, name: str) -> str:
        self.resolve(name)
        return self.kinds[name]


def compile_spec(raw: dict | MetricSpec) -> CompiledSpec:
    spec = raw if isinstance(raw, MetricSpec) else parse_spec(raw)
    if spec.declared_cannot_express:
        raise CannotExpress(spec.declared_cannot_express)
    if spec.population not in POPULATIONS:
        suggestion = difflib.get_close_matches(spec.population or "", list(POPULATIONS), n=3, cutoff=0.4)
        hint = f"Closest names: {', '.join(suggestion)}." if suggestion else ""
        raise CannotExpress(
            f"unknown population {spec.population!r}. Known populations: {', '.join(sorted(POPULATIONS))}.",
            hint=hint)
    pop = POPULATIONS[spec.population]
    link = None
    if spec.link is not None:
        if spec.link not in LINKS:
            suggestion = difflib.get_close_matches(spec.link, list(LINKS), n=3, cutoff=0.4)
            hint = f"Closest names: {', '.join(suggestion)}." if suggestion else ""
            raise CannotExpress(f"unknown link {spec.link!r}. Known links: {', '.join(sorted(LINKS))}.", hint=hint)
        link = LINKS[spec.link]
        if spec.population not in link.base_populations:
            raise CannotExpress(
                f"link {link.name} does not apply to population {spec.population}. "
                f"It applies to: {', '.join(sorted(link.base_populations))}.")
        for metric in spec.metrics:
            _reject_fanout(metric, link.fanout_claim_measures)

    filters = list(spec.filters)
    notes = [pop.note] if pop.note else [pop.description]
    if link is not None:
        notes.append(link.note)

    if pop.kind == "parameterized":
        from_sql, filters, param_note = _parameterized_from(pop, filters)
        notes.append(param_note)
    elif pop.kind == "derived":
        from_sql = f"(\n{pop.derived_sql}\n) AS spec_pop"
    else:
        from_sql = f"{pop.relation} AS spec_pop"

    binding = _binding(pop)
    referenced = _referenced(spec, filters)
    if link is not None:
        from_sql, binding = _apply_link(link, from_sql, binding, referenced)
        if link.name == "billed_procedures":
            notes.append("each claim is repeated once for every distinct procedure on its latest submission")
        if link.name == "primary_billed_attributes":
            used = [name for name in PRIMARY_ATTRIBUTE_FIELDS if name in referenced]
            if len(used) > 1:
                notes.append("a claim+payer with more than one distinct requested attribute value is counted once per value")
        if link.fanout_claim_measures:
            notes.append("count(*) counts adjustment rows. count_distinct of remit_claim_id counts adjudications")

    where = []
    if pop.where_sql and pop.kind == "table":
        where.append(pop.where_sql)
        notes.append(f"population predicate: {pop.where_sql.removeprefix('spec_pop.')}")
    for item in filters:
        where.append(_compile_filter(item, binding))
        notes.append(f"filter: {item.field} {item.op} {_shown(item.value)}".strip())
    if spec.time_window is not None:
        where.extend(_compile_time(spec.time_window, binding))
        window = spec.time_window
        notes.append(
            f"time window: {window.field} from {window.start or 'the start'} to {window.end or 'the end'}"
            + (" exclusive" if window.end_exclusive else " inclusive"))

    scalar = spec.shape == "aggregate" and not spec.grain
    if spec.shape == "detail":
        select_sql, output, group_exprs, having_sql, order_sql = _detail(spec, binding)
        body = _select_body(select_sql, from_sql, where, [], None, order_sql if spec.ties != "include_boundary" else None,
                            spec.limit if spec.ties == "strict" else None)
    else:
        select_items = []
        output = []
        group_exprs = []
        raw: dict[str, str] = {}
        rounded = _rounded_aliases(spec)
        for grain in spec.grain:
            expr = _grain_expr(grain, binding)
            group_exprs.append(expr)
            select_items.append(f"{expr} AS {_quote(grain.alias)}")
            output.append(grain.alias)
        for metric in spec.metrics:
            raw[metric.alias] = _metric_expr(metric, binding, scalar)
            shown = raw[metric.alias]
            if metric.alias in rounded:
                shown = f"round({shown}, {spec.rounding.digits})"
            select_items.append(f"{shown} AS {_quote(metric.alias)}")
            output.append(metric.alias)
        having_sql = None
        if spec.having:
            having_sql = " AND ".join(
                f"{raw[item.alias]} {item.op} {_sql_literal(item.value)}" for item in spec.having)
            notes.append("having uses the value before rounding")
        if scalar and any(metric.agg == "sum" for metric in spec.metrics):
            notes.append("a scalar sum with no matching rows is 0")
        if any(metric.coalesce_zero for metric in spec.metrics):
            notes.append("coalesce_zero replaces a null sum with 0")
        if rounded:
            notes.append(f"rounded to {spec.rounding.digits} digits: {', '.join(sorted(rounded))}")
        order_sql = _order_sql(spec.order_by, output)
        limit = spec.limit if spec.ties == "strict" else None
        body = _select_body(select_items, from_sql, where, group_exprs, having_sql,
                            order_sql if spec.ties != "include_boundary" else None, limit)
        select_sql = select_items

    if spec.ties == "include_boundary":
        grouped = body if spec.shape == "detail" else _select_body(
            select_sql, from_sql, where, group_exprs, having_sql, None, None)
        body = _with_ties(grouped, output, _ranking_order_sql(spec, output), order_sql, spec.limit)
        notes.append(
            "ties include_boundary ranks on the ordered metrics and keeps every row tied with the Nth. "
            "Grain columns in order_by only order the display and the result can be longer than the limit")
    elif spec.ties == "strict":
        notes.append("ties strict keeps the limit after the order_by tie-break")

    sql = _pretty(body)
    try:
        guard(sql, 1000, False)
    except GuardError as exc:
        raise RuntimeError(f"compiler emitted SQL the guard rejected: {exc}\n{sql}") from exc
    return CompiledSpec(sql, pop.name, spec.link, [note for note in notes if note], output)


def _parameterized_from(pop: Population, filters: list[Filter]) -> tuple[str, list[Filter], str]:
    if pop.name != "secondary_current_for_primary":
        raise CannotExpress(f"population {pop.name} has no compiler rule")
    taken = [item for item in filters if item.field == "primary_payer_name"]
    rest = [item for item in filters if item.field != "primary_payer_name"]
    if len(taken) != 1 or taken[0].op != "=" or not isinstance(taken[0].value, str) or not taken[0].value:
        raise CannotExpress(
            "population secondary_current_for_primary needs exactly one filter "
            "primary_payer_name equals the primary payer's stored name")
    payer = _sql_literal(taken[0].value)
    derived = f"""
SELECT r.*
FROM sem.remit_claims AS r
JOIN (
    SELECT DISTINCT claim_key
    FROM sem.claims
    WHERE payer_responsibility = 'P' AND payer_name = {payer}
) AS spec_prim ON spec_prim.claim_key = r.claim_key
WHERE r.is_current_adjudication AND r.payer_sequence = 'secondary'
""".strip()
    return f"(\n{derived}\n) AS spec_pop", rest, f"primary payer name {taken[0].value}"


def _binding(pop: Population) -> Binding:
    exprs: dict[str, str] = {}
    kinds: dict[str, str] = {}
    virtual = dict(pop.virtual)
    for name in population_columns(pop):
        exprs[name] = virtual.get(name, f"spec_pop.{name}")
        kinds[name] = field_kind(name)
    return Binding(exprs, kinds, pop.modifier_columns)


def _referenced(spec: MetricSpec, filters: list[Filter]) -> set[str]:
    names: set[str] = set()

    def walk(metric: Metric) -> None:
        if metric.field:
            names.add(metric.field)
        for item in metric.where:
            if item.field != "modifiers":
                names.add(item.field)
        if metric.numerator is not None:
            walk(metric.numerator)
            walk(metric.denominator)

    for metric in spec.metrics:
        walk(metric)
    for item in filters:
        if item.field not in ("modifiers", "primary_payer_name"):
            names.add(item.field)
    for grain in spec.grain:
        names.add(grain.field)
    if spec.time_window is not None:
        names.add(spec.time_window.field)
    names.update(spec.columns)
    return names


def _apply_link(link, from_sql: str, binding: Binding, referenced: set[str]) -> tuple[str, Binding]:
    exprs = dict(binding.exprs)
    kinds = dict(binding.kinds)
    if link.name == "adjudication_adjustments":
        from_sql += (
            "\nJOIN sem.adjustments AS spec_adj"
            "\n  ON spec_adj.remit_claim_id = spec_pop.remit_claim_id"
        )
        for name in link.adds:
            if name not in exprs:
                exprs[name] = f"spec_adj.{name}"
                kinds[name] = field_kind(name)
    elif link.name == "primary_billed_attributes":
        attrs = [name for name in PRIMARY_ATTRIBUTE_FIELDS if name in referenced]
        columns = ["claim_key", "payer_key", *attrs]
        from_sql += (
            "\nJOIN (\n  SELECT DISTINCT " + ", ".join(columns) +
            "\n  FROM sem.claims\n  WHERE payer_responsibility = 'P'\n) AS spec_primary"
            "\n  ON spec_primary.claim_key = spec_pop.claim_key"
            "\n AND spec_primary.payer_key = spec_pop.payer_key"
        )
        for name in attrs:
            exprs[name] = f"spec_primary.{name}"
            kinds[name] = field_kind(name)
    elif link.name == "billed_procedures":
        from_sql += (
            "\nJOIN (\n  SELECT DISTINCT claim_key, procedure_code\n  FROM sem.claim_lines"
            "\n  WHERE is_latest_submission\n) AS spec_billed"
            "\n  ON spec_billed.claim_key = spec_pop.claim_key"
        )
        exprs["procedure_code"] = "spec_billed.procedure_code"
        kinds["procedure_code"] = field_kind("procedure_code")
    else:
        raise CannotExpress(f"link {link.name} has no compiler rule")
    return from_sql, Binding(exprs, kinds, binding.modifier_columns)


def _reject_fanout(metric: Metric, enabled: bool) -> None:
    if not enabled:
        return
    if metric.agg in ("sum", "avg", "min", "max") and metric.field in CLAIM_LEVEL_MEASURES:
        raise CannotExpress(
            f"cannot {metric.agg} {metric.field} through link adjudication_adjustments: "
            "the claim amount would repeat once per adjustment row. "
            "Aggregate the claim in a separate spec, or count_distinct remit_claim_id.")
    if metric.agg == "ratio":
        _reject_fanout(metric.numerator, True)
        _reject_fanout(metric.denominator, True)


def _compile_filter(item: Filter, binding: Binding) -> str:
    if item.op not in FILTER_OPS:
        raise CannotExpress(f"filter op {item.op!r} is not supported. Supported ops: {', '.join(FILTER_OPS)}.")
    if item.op == "in_modifiers":
        if item.field != "modifiers":
            raise CannotExpress("op in_modifiers requires field modifiers")
        if not binding.modifier_columns:
            raise CannotExpress("this population has no modifier columns", hint=FIELD_HINTS["modifiers"])
        values = item.value if isinstance(item.value, list) else [item.value]
        if not values:
            raise CannotExpress("in_modifiers needs at least one modifier")
        columns = ", ".join(f"spec_pop.{name}" for name in binding.modifier_columns)
        return "(" + " OR ".join(f"{_sql_literal(value)} IN ({columns})" for value in values) + ")"
    if item.op in ("is_true", "is_false", "is_null", "is_not_null"):
        column = binding.resolve(item.field)
        if item.op in ("is_true", "is_false") and binding.kind(item.field) != "boolean":
            raise CannotExpress(f"{item.field} is not a boolean; use a comparison")
        if item.op == "is_true":
            return column
        if item.op == "is_false":
            return f"NOT {column}"
        if item.op == "is_null":
            return f"{column} IS NULL"
        return f"{column} IS NOT NULL"
    column = binding.resolve(item.field)
    kind = binding.kind(item.field)
    if kind == "list":
        raise CannotExpress(f"cannot filter list field {item.field} directly")
    if item.op in ("in", "not_in"):
        if not isinstance(item.value, list) or not item.value:
            raise CannotExpress(f"{item.op} needs a non-empty list")
        literals = ", ".join(_sql_literal(value, date_field=kind == "date") for value in item.value)
        keyword = "NOT IN" if item.op == "not_in" else "IN"
        return f"{column} {keyword} ({literals})"
    if item.op == "between":
        if not isinstance(item.value, list) or len(item.value) != 2:
            raise CannotExpress("between needs a two-item list [low, high]")
        low, high = item.value
        return (f"{column} BETWEEN {_sql_literal(low, date_field=kind == 'date')} "
                f"AND {_sql_literal(high, date_field=kind == 'date')}")
    if item.op not in ("=", "!=", ">", ">=", "<", "<="):
        raise CannotExpress(f"filter op {item.op!r} is not supported")
    return f"{column} {item.op} {_sql_literal(item.value, date_field=kind == 'date')}"


def _compile_time(window, binding: Binding) -> list[str]:
    if binding.kind(window.field) != "date":
        raise CannotExpress(f"time window field {window.field} is not a date")
    column = binding.resolve(window.field)
    parts = []
    if window.start is not None:
        parts.append(f"{column} >= {_sql_literal(window.start, date_field=True)}")
    if window.end is not None:
        op = "<" if window.end_exclusive else "<="
        parts.append(f"{column} {op} {_sql_literal(window.end, date_field=True)}")
    return parts


def _grain_expr(grain, binding: Binding) -> str:
    kind = binding.kind(grain.field)
    if kind == "list":
        raise CannotExpress(f"{grain.field} is a list and cannot be a grain")
    column = binding.resolve(grain.field)
    if grain.bucket:
        if kind != "date":
            raise CannotExpress(f"grain bucket {grain.bucket} requires a date field, and {grain.field} is not one")
        if grain.bucket not in BUCKETS:
            raise CannotExpress(f"grain bucket must be one of {', '.join(BUCKETS)}")
        return f"date_trunc('{grain.bucket}', {column})::DATE"
    return column


def _metric_expr(metric: Metric, binding: Binding, scalar: bool) -> str:
    if metric.agg not in AGGS:
        raise CannotExpress(
            f"aggregation {metric.agg!r} is not in the spec. Supported aggregations: {', '.join(AGGS)}. "
            "Median, quantile, and windowed change are not expressible.")
    where_sql = " AND ".join(_compile_filter(item, binding) for item in metric.where) if metric.where else ""

    def filtered(expr: str) -> str:
        return f"{expr} FILTER (WHERE {where_sql})" if where_sql else expr

    if metric.agg == "count":
        return filtered("count(*)")
    if metric.agg == "count_distinct":
        if binding.kind(metric.field) == "list":
            raise CannotExpress(f"cannot count distinct list field {metric.field}")
        return filtered(f"count(DISTINCT {binding.resolve(metric.field)})")
    if metric.agg == "ratio":
        numerator = _metric_expr(metric.numerator, binding, scalar)
        denominator = _metric_expr(metric.denominator, binding, scalar)
        scale = "100.0 * " if metric.scale == "percent" else ""
        return f"({scale}({numerator})::DOUBLE / nullif(({denominator})::DOUBLE, 0))"
    kind = binding.kind(metric.field)
    if kind == "list" or (kind in ("text", "date") and metric.agg in ("sum", "avg")):
        raise CannotExpress(f"cannot {metric.agg} {kind} field {metric.field}")
    column = binding.resolve(metric.field)
    if kind == "boolean" and metric.agg in ("sum", "avg"):
        inner = f"(CASE WHEN {column} THEN 1 WHEN NOT {column} THEN 0 END)"
    else:
        inner = column
    expr = filtered(f"{metric.agg}({inner})")
    if metric.sign == "flip":
        expr = f"-({expr})"
    coalesce_zero = metric.coalesce_zero if metric.coalesce_zero is not None else (metric.agg == "sum" and scalar)
    if coalesce_zero:
        expr = f"coalesce({expr}, 0)"
    return expr


def _rounded_aliases(spec: MetricSpec) -> set[str]:
    if spec.rounding is None:
        return set()
    if spec.rounding.aliases is not None:
        return set(spec.rounding.aliases)
    return {metric.alias for metric in spec.metrics if metric.agg in ("sum", "avg", "ratio")}


def _detail(spec: MetricSpec, binding: Binding):
    items = []
    output = []
    for name in spec.columns:
        if binding.kind(name) == "list":
            raise CannotExpress(f"detail output cannot include list field {name}")
        items.append(f"{binding.resolve(name)} AS {_quote(name)}")
        output.append(name)
    order_sql = _order_sql(spec.order_by, output)
    return items, output, [], None, order_sql


def _order_sql(order_by: list[Order], output: list[str]) -> str | None:
    if not order_by:
        return None
    missing = [item.field for item in order_by if item.field not in output]
    if missing:
        raise CannotExpress(
            f"order_by {missing} is not an output column. Output columns: {', '.join(output)}.")
    return ", ".join(f"{_quote(item.field)} {item.direction.upper()}" for item in order_by)


def _ranking_order_sql(spec: MetricSpec, output: list[str]) -> str | None:
    """Rank on the ordered metrics. Grain columns in order_by are the display tie-break."""
    grain_aliases = {grain.alias for grain in spec.grain}
    ranking = [item for item in spec.order_by if item.field not in grain_aliases]
    if not ranking:
        ranking = list(spec.order_by)
    return _order_sql(ranking, output)


def _select_body(select_items, from_sql, where, group_exprs, having_sql, order_sql, limit) -> str:
    sql = "SELECT\n  " + ",\n  ".join(select_items) + f"\nFROM {from_sql}"
    if where:
        sql += "\nWHERE " + "\n  AND ".join(where)
    if group_exprs:
        sql += "\nGROUP BY " + ", ".join(group_exprs)
    if having_sql:
        sql += f"\nHAVING {having_sql}"
    if order_sql:
        sql += f"\nORDER BY {order_sql}"
    if limit is not None:
        sql += f"\nLIMIT {int(limit)}"
    return sql


def _with_ties(grouped_sql: str, output: list[str], rank_sql: str | None,
               order_sql: str | None, limit: int | None) -> str:
    if not rank_sql or not order_sql or limit is None:
        raise SpecError("include_boundary needs order_by and limit")
    chosen = ", ".join(_quote(name) for name in output)
    return (
        "WITH spec_grouped AS (\n"
        + _indent(grouped_sql) +
        "\n),\nspec_ranked AS (\n"
        f"  SELECT spec_grouped.*, rank() OVER (ORDER BY {rank_sql}) AS spec_rank\n"
        "  FROM spec_grouped\n"
        ")\n"
        f"SELECT {chosen}\n"
        "FROM spec_ranked\n"
        f"WHERE spec_rank <= {int(limit)}\n"
        f"ORDER BY {order_sql}"
    )


def _sql_literal(value, date_field: bool = False) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CannotExpress("filter values must be finite numbers")
        return repr(value)
    if isinstance(value, str):
        if date_field:
            if not _DATE.fullmatch(value):
                raise CannotExpress(f"date value {value!r} must be YYYY-MM-DD")
            return f"DATE '{value}'"
        if "\x00" in value:
            raise CannotExpress("filter values cannot contain a NUL character")
        return "'" + value.replace("'", "''") + "'"
    raise CannotExpress(f"value type {type(value).__name__} is not supported in a spec")


def _quote(alias: str) -> str:
    return '"' + alias.replace('"', "") + '"'


def _shown(value) -> str:
    if isinstance(value, str) and len(value) > 80:
        return value[:77] + "..."
    return repr(value)


def _indent(sql: str) -> str:
    return "\n".join("  " + line if line else line for line in sql.splitlines())


def _pretty(sql: str) -> str:
    return sql.strip() + "\n"

