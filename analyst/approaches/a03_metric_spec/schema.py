"""Parse a metric spec. A shape error is a SpecError. An unsupported but well-formed request is left for the compiler to reject as CannotExpress."""
from __future__ import annotations

import re
from dataclasses import dataclass
from dataclasses import field as dc_field
from typing import Any

from .catalog import LINKS, POPULATIONS
from .compiler_errors import SpecError

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
AGGS = ("count", "count_distinct", "sum", "avg", "min", "max", "ratio")
FILTER_OPS = ("=", "!=", ">", ">=", "<", "<=", "in", "not_in", "between", "is_true", "is_false",
              "is_null", "is_not_null", "in_modifiers")
BUCKETS = ("day", "week", "month", "quarter", "year")
RESERVED_ALIASES = frozenset({
    "spec_rank", "spec_pop", "spec_adj", "spec_primary", "spec_billed", "spec_grouped",
    "spec_ranked", "spec_inner", "spec_prim",
})
_TOP_KEYS = frozenset({
    "population", "metrics", "filters", "grain", "time_window", "having", "order_by",
    "limit", "ties", "rounding", "link", "shape", "columns", "status", "because",
})
_METRIC_KEYS = frozenset({
    "alias", "agg", "field", "where", "sign", "numerator", "denominator", "scale", "coalesce_zero",
})
_FILTER_KEYS = frozenset({"field", "op", "value"})


@dataclass
class Filter:
    field: str
    op: str
    value: Any = None


@dataclass
class Metric:
    alias: str
    agg: str
    field: str | None = None
    where: list[Filter] = dc_field(default_factory=list)
    sign: str = "as_stored"
    numerator: Metric | None = None
    denominator: Metric | None = None
    scale: str = "fraction"
    coalesce_zero: bool | None = None


@dataclass
class Grain:
    field: str
    bucket: str | None = None

    @property
    def alias(self) -> str:
        return f"{self.field}_{self.bucket}" if self.bucket else self.field


@dataclass
class Having:
    alias: str
    op: str
    value: Any


@dataclass
class Order:
    field: str
    direction: str


@dataclass
class TimeWindow:
    field: str
    start: str | None = None
    end: str | None = None
    end_exclusive: bool = False


@dataclass
class Rounding:
    digits: int
    aliases: list[str] | None = None


@dataclass
class MetricSpec:
    population: str | None
    metrics: list[Metric]
    filters: list[Filter]
    grain: list[Grain]
    time_window: TimeWindow | None
    having: list[Having]
    order_by: list[Order]
    limit: int | None
    ties: str | None
    rounding: Rounding | None
    link: str | None
    shape: str
    columns: list[str]
    declared_cannot_express: str | None = None


def _obj(value: Any, what: str) -> dict:
    if not isinstance(value, dict):
        raise SpecError(f"{what} must be a JSON object")
    return value


def _keys(obj: dict, allowed: frozenset[str], what: str) -> None:
    unknown = set(obj) - allowed
    if unknown:
        raise SpecError(f"{what} has unexpected keys {sorted(unknown)}. Allowed keys: {sorted(allowed)}")


def _ident(value: Any, what: str) -> str:
    if not isinstance(value, str) or not _IDENT.fullmatch(value):
        raise SpecError(f"{what} must be an identifier")
    if value in RESERVED_ALIASES:
        raise SpecError(f"{what} {value!r} is reserved")
    return value


def _parse_filter(raw: Any) -> Filter:
    obj = _obj(raw, "filter")
    _keys(obj, _FILTER_KEYS, "filter")
    if "field" not in obj or "op" not in obj:
        raise SpecError("filter needs field and op")
    field_name = _ident(obj["field"], "filter field")
    op = obj["op"]
    if not isinstance(op, str):
        raise SpecError("filter op must be a string")
    if op not in FILTER_OPS:
        # A well-formed request for an operator the language does not have.
        # Raised later as CannotExpress so the SQL gate can open. Stash it as the op
        # and let the compiler reject it. Shape-invalid ops (non-strings) already failed.
        pass
    if op in ("is_true", "is_false", "is_null", "is_not_null"):
        if "value" in obj:
            raise SpecError(f"filter op {op} does not take a value")
        return Filter(field_name, op)
    if "value" not in obj:
        raise SpecError(f"filter op {op} needs a value")
    return Filter(field_name, op, obj["value"])


def _parse_metric(raw: Any, *, nested: bool) -> Metric:
    obj = _obj(raw, "metric")
    _keys(obj, _METRIC_KEYS, "metric")
    if "agg" not in obj or not isinstance(obj["agg"], str):
        raise SpecError("metric needs an agg string")
    agg = obj["agg"]
    alias = obj.get("alias")
    if nested:
        alias = alias or f"nested_{agg}"
        if not isinstance(alias, str) or not _IDENT.fullmatch(alias):
            raise SpecError("nested metric alias must be an identifier when it is set")
    else:
        if not isinstance(alias, str):
            raise SpecError("metric needs an alias")
        _ident(alias, "metric alias")
    where = [_parse_filter(item) for item in obj.get("where", [])]
    if "where" in obj and not isinstance(obj["where"], list):
        raise SpecError("metric where must be a list of filters")
    sign = obj.get("sign", "as_stored")
    if sign not in ("as_stored", "flip"):
        raise SpecError("metric sign must be as_stored or flip")
    scale = obj.get("scale", "fraction")
    if scale not in ("fraction", "percent"):
        raise SpecError("metric scale must be fraction or percent")
    field_name = obj.get("field")
    if field_name is not None:
        field_name = _ident(field_name, "metric field")
    numerator = _parse_metric(obj["numerator"], nested=True) if "numerator" in obj else None
    denominator = _parse_metric(obj["denominator"], nested=True) if "denominator" in obj else None
    if agg == "ratio":
        if numerator is None or denominator is None:
            raise SpecError("a ratio needs numerator and denominator")
        if numerator.agg == "ratio" or denominator.agg == "ratio":
            raise SpecError("a ratio cannot contain another ratio")
        if field_name is not None:
            raise SpecError("a ratio does not take a field; use numerator and denominator")
    elif agg in ("count",):
        if field_name is not None:
            raise SpecError("count does not take a field; use count_distinct to count distinct values")
        if numerator or denominator:
            raise SpecError("count does not take numerator or denominator")
    elif agg == "count_distinct":
        if field_name is None:
            raise SpecError("count_distinct needs a field")
    elif agg in ("sum", "avg", "min", "max"):
        if field_name is None:
            raise SpecError(f"{agg} needs a field")
        if numerator or denominator:
            raise SpecError(f"{agg} does not take numerator or denominator")
    if sign == "flip" and agg != "sum":
        raise SpecError("sign flip is only valid on a sum")
    if scale != "fraction" and agg != "ratio":
        raise SpecError("scale is only valid on a ratio")
    coalesce_zero = obj.get("coalesce_zero")
    if "coalesce_zero" in obj and not isinstance(coalesce_zero, bool):
        raise SpecError("coalesce_zero must be a boolean")
    if coalesce_zero and agg != "sum":
        raise SpecError("coalesce_zero is only valid on a sum")
    return Metric(alias, agg, field_name, where, sign, numerator, denominator, scale, coalesce_zero)


def _parse_grain(raw: Any) -> Grain:
    if isinstance(raw, str):
        return Grain(_ident(raw, "grain"))
    obj = _obj(raw, "grain")
    _keys(obj, frozenset({"field", "bucket"}), "grain")
    if "field" not in obj:
        raise SpecError("grain needs a field")
    bucket = obj.get("bucket")
    if bucket is not None and bucket not in BUCKETS:
        raise SpecError(f"grain bucket must be one of {BUCKETS}")
    return Grain(_ident(obj["field"], "grain field"), bucket)


def parse_spec(raw: Any) -> MetricSpec:
    """Parse JSON-compatible data into a spec. Does not resolve columns."""
    obj = _obj(raw, "spec")
    _keys(obj, _TOP_KEYS, "spec")
    if obj.get("status") == "cannot_express":
        because = obj.get("because")
        if not isinstance(because, str) or not because.strip():
            raise SpecError("status cannot_express needs a because string")
        extra = set(obj) - {"status", "because"}
        if extra:
            raise SpecError(f"a cannot_express declaration only takes status and because, not {sorted(extra)}")
        return MetricSpec(None, [], [], [], None, [], [], None, None, None, None, "aggregate", [], because.strip())
    if "status" in obj:
        raise SpecError("status must be cannot_express, or omit status and send a population")
    if "population" not in obj or not isinstance(obj["population"], str):
        raise SpecError("spec needs a population name")
    shape = obj.get("shape", "aggregate")
    if shape not in ("aggregate", "detail"):
        raise SpecError("shape must be aggregate or detail")
    filters = obj.get("filters", [])
    if not isinstance(filters, list):
        raise SpecError("filters must be a list")
    grain_raw = obj.get("grain", [])
    if not isinstance(grain_raw, list):
        raise SpecError("grain must be a list")
    having_raw = obj.get("having", [])
    if not isinstance(having_raw, list):
        raise SpecError("having must be a list")
    order_raw = obj.get("order_by", [])
    if not isinstance(order_raw, list):
        raise SpecError("order_by must be a list")
    columns = obj.get("columns", [])
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise SpecError("columns must be a list of field names")
    metrics_raw = obj.get("metrics", [])
    if shape == "detail":
        if metrics_raw:
            raise SpecError("detail shape takes columns, not metrics")
        if not columns:
            raise SpecError("detail shape needs columns")
        if grain_raw or having_raw:
            raise SpecError("detail shape does not take grain or having")
    else:
        if columns:
            raise SpecError("aggregate shape takes metrics, not columns")
        if not isinstance(metrics_raw, list) or not metrics_raw:
            raise SpecError("aggregate shape needs at least one metric")
    metrics = [_parse_metric(item, nested=False) for item in metrics_raw]
    aliases = [m.alias for m in metrics]
    if len(aliases) != len(set(aliases)):
        raise SpecError("metric aliases must be unique")
    grain = [_parse_grain(item) for item in grain_raw]
    if len({g.alias for g in grain}) != len(grain):
        raise SpecError("grain fields must be unique")
    if set(aliases) & {g.alias for g in grain}:
        raise SpecError("a metric alias collides with a grain column")
    having = []
    for item in having_raw:
        body = _obj(item, "having")
        _keys(body, frozenset({"alias", "op", "value"}), "having")
        if not isinstance(body.get("op"), str) or body["op"] not in ("=", "!=", ">", ">=", "<", "<="):
            raise SpecError("having op must be a comparison")
        if "value" not in body:
            raise SpecError("having needs a value")
        alias = _ident(body.get("alias"), "having alias")
        if alias not in aliases:
            raise SpecError(f"having alias {alias!r} is not a metric in this spec")
        having.append(Having(alias, body["op"], body["value"]))
    order_by = []
    for item in order_raw:
        body = _obj(item, "order_by")
        _keys(body, frozenset({"field", "direction"}), "order_by")
        direction = body.get("direction", "asc")
        if direction not in ("asc", "desc"):
            raise SpecError("order direction must be asc or desc")
        order_by.append(Order(_ident(body.get("field"), "order field"), direction))
    limit = obj.get("limit")
    if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 1000):
        raise SpecError("limit must be an integer from 1 to 1000")
    ties = obj.get("ties")
    if ties is not None and ties not in ("strict", "include_boundary"):
        raise SpecError("ties must be strict or include_boundary")
    if limit is not None and not order_by:
        raise SpecError("limit needs order_by so the row set is deterministic")
    if limit is not None and ties is None:
        raise SpecError("limit needs ties: strict or include_boundary")
    if ties is not None and limit is None:
        raise SpecError("ties is only meaningful with a limit")
    if shape == "aggregate" and order_by and not grain:
        raise SpecError("order_by on an aggregate needs a grain; a scalar has one row")
    time_window = None
    if "time_window" in obj and obj["time_window"] is not None:
        tw = _obj(obj["time_window"], "time_window")
        _keys(tw, frozenset({"field", "start", "end", "end_exclusive"}), "time_window")
        if "field" not in tw:
            raise SpecError("time_window needs a field")
        if tw.get("start") is None and tw.get("end") is None:
            raise SpecError("time_window needs a start, an end, or both")
        for bound in ("start", "end"):
            if tw.get(bound) is not None and not isinstance(tw[bound], str):
                raise SpecError(f"time_window {bound} must be a YYYY-MM-DD string")
        end_exclusive = tw.get("end_exclusive", False)
        if not isinstance(end_exclusive, bool):
            raise SpecError("time_window end_exclusive must be a boolean")
        time_window = TimeWindow(_ident(tw["field"], "time field"), tw.get("start"), tw.get("end"), end_exclusive)
    rounding = None
    if "rounding" in obj and obj["rounding"] is not None:
        rnd = _obj(obj["rounding"], "rounding")
        _keys(rnd, frozenset({"digits", "aliases"}), "rounding")
        digits = rnd.get("digits")
        if not isinstance(digits, int) or isinstance(digits, bool) or not 0 <= digits <= 6:
            raise SpecError("rounding digits must be an integer from 0 to 6")
        aliases_r = rnd.get("aliases")
        if aliases_r is not None:
            if not isinstance(aliases_r, list) or not all(isinstance(a, str) for a in aliases_r):
                raise SpecError("rounding aliases must be a list of metric aliases")
            missing = [a for a in aliases_r if a not in aliases]
            if missing:
                raise SpecError(f"rounding aliases {missing} are not metrics in this spec")
        rounding = Rounding(digits, aliases_r)
    link = obj.get("link")
    if link is not None:
        if not isinstance(link, str):
            raise SpecError("link must be a name")
        if shape == "detail":
            raise SpecError("detail shape does not take a link")
    return MetricSpec(
        population=obj["population"],
        metrics=metrics,
        filters=[_parse_filter(item) for item in filters],
        grain=grain,
        time_window=time_window,
        having=having,
        order_by=order_by,
        limit=limit,
        ties=ties,
        rounding=rounding,
        link=link,
        shape=shape,
        columns=[_ident(c, "column") for c in columns],
    )


def tool_input_schema() -> dict:
    """JSON schema for the compile_metric_spec tool. Enums come from the catalog."""
    population_names = sorted(POPULATIONS)
    link_names = sorted(LINKS)
    filter_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["field", "op"],
        "properties": {
            "field": {"type": "string"},
            "op": {"type": "string", "enum": list(FILTER_OPS)},
            "value": {},
        },
    }
    metric_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["alias", "agg"],
        "properties": {
            "alias": {"type": "string"},
            "agg": {"type": "string", "enum": list(AGGS)},
            "field": {"type": "string"},
            "where": {"type": "array", "items": filter_schema},
            "sign": {"type": "string", "enum": ["as_stored", "flip"]},
            "scale": {"type": "string", "enum": ["fraction", "percent"]},
            "coalesce_zero": {"type": "boolean"},
            "numerator": {"type": "object"},
            "denominator": {"type": "object"},
        },
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "status": {"type": "string", "enum": ["cannot_express"]},
            "because": {"type": "string"},
            "population": {"type": "string", "enum": population_names},
            "shape": {"type": "string", "enum": ["aggregate", "detail"]},
            "link": {"type": "string", "enum": link_names},
            "metrics": {"type": "array", "items": metric_schema},
            "columns": {"type": "array", "items": {"type": "string"}},
            "filters": {"type": "array", "items": filter_schema},
            "grain": {"type": "array", "items": {"oneOf": [
                {"type": "string"},
                {"type": "object", "additionalProperties": False, "required": ["field"],
                 "properties": {"field": {"type": "string"}, "bucket": {"type": "string", "enum": list(BUCKETS)}}},
            ]}},
            "time_window": {
                "type": "object", "additionalProperties": False, "required": ["field"],
                "properties": {
                    "field": {"type": "string"},
                    "start": {"type": "string"},
                    "end": {"type": "string"},
                    "end_exclusive": {"type": "boolean"},
                },
            },
            "having": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["alias", "op", "value"],
                "properties": {"alias": {"type": "string"}, "op": {"type": "string"}, "value": {}},
            }},
            "order_by": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["field"],
                "properties": {
                    "field": {"type": "string"},
                    "direction": {"type": "string", "enum": ["asc", "desc"]},
                },
            }},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000},
            "ties": {"type": "string", "enum": ["strict", "include_boundary"]},
            "rounding": {
                "type": "object", "additionalProperties": False, "required": ["digits"],
                "properties": {
                    "digits": {"type": "integer", "minimum": 0, "maximum": 6},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
    }
