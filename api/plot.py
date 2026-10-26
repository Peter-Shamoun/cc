"""Strict PlotSpec v1 (contract 1.18.0, CONTRACT.md §16.1–16.2).

One closed, versioned schema (``$defs.PlotSpec`` in ``api/contract/v1.schema.json``) is the source of truth. The web
mirror is the zod schema in ``web/src/contract/plot.ts``; both run ``api/contract/plot_cases.json`` and must report
exactly the same rule ids.

    check_plot(plot)              -> sorted list of rule ids ([] when valid)
    check_plot(header, header=True)  for a ``chart.start`` header (no ``data``)
    to_vega_lite(plot)            -> the derived Vega-Lite spec for pre-1.18 clients
    gate_parts(parts)             -> parts with every invalid chart dropped (the table stays) and the rule ids

Validation runs in two stages. Stage 1 is structural (the JSON Schema plus non-finite numbers, which JSON Schema
cannot see). Stage 2 (semantic: field references, series cap, temporal values) runs only if stage 1 passed, on both
sides, so the two validators agree on every input.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache

from api.contract import schema

#: Rule ids shared with web/src/contract/plot.ts. Each one is a reason a plot is rejected.
RULES = {
    "plot.type": "a value has the wrong JSON type (incl. a non-numeric y or quantitative x)",
    "plot.required": "a required field is missing",
    "plot.closed": "an unknown field (every PlotSpec object is closed)",
    "plot.version": "`v` is not a PlotSpec version this side knows",
    "plot.kind": "`kind` is not an allowed chart kind",
    "plot.enum": "an axis type, format or sort is not in its enum",
    "plot.label.length": "a title, axis title or string value is too long (or the title is empty)",
    "plot.field.name": "a field name is not an identifier of at most 64 characters",
    "plot.points.max": "more than maxPoints points",
    "plot.points.min": "no points",
    "plot.number.finite": "a number is NaN or infinite",
    "plot.field.duplicate": "x, y and series must name different fields",
    "plot.field.unknown": "a row has a key that is not the x, y or series field",
    "plot.row.missing": "a row lacks x or y, or the series value",
    "plot.series.max": "more than maxSeries distinct series values",
    "plot.temporal": "a temporal x value is not an ISO date (YYYY-MM[-DD[THH:MM[:SS[.f]]][Z|±HH:MM]])",
}

#: ASCII digits only, and ``(?![\s\S])`` instead of ``$`` (Python's ``$`` also matches before a final newline)
TEMPORAL = re.compile(schema()["$defs"]["PlotSpec"]["x-temporal"])


def caps() -> dict:
    return schema()["$defs"]["PlotSpec"]["x-caps"]


@lru_cache(maxsize=2)
def _validator(header: bool):
    from jsonschema import Draft202012Validator
    root = {"$defs": schema()["$defs"], "$ref": "#/$defs/" + ("PlotHeader" if header else "PlotSpec")}
    return Draft202012Validator(root)


def _rule(err) -> str:
    kw, path = err.validator, list(err.absolute_path)
    if kw == "additionalProperties":
        return "plot.closed"
    if kw == "required":
        return "plot.required"
    if kw == "type":
        return "plot.type"
    if kw == "const":
        return "plot.version" if path == ["v"] else "plot.enum"
    if kw == "enum":
        return "plot.kind" if path == ["kind"] else "plot.enum"
    if kw in ("maxLength", "minLength"):
        return "plot.label.length"
    if kw in ("pattern", "propertyNames"):
        return "plot.field.name"
    if kw == "maxItems":
        return "plot.points.max"
    if kw == "minItems":
        return "plot.points.min"
    return "plot.type"  # $ref/anyOf fallbacks: a shape mismatch


def _nonfinite(rows) -> bool:
    if not isinstance(rows, list):
        return False
    return any(isinstance(v, float) and not math.isfinite(v)
               for r in rows if isinstance(r, dict) for v in r.values())


def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _semantic(plot: dict) -> set[str]:
    out: set[str] = set()
    x, y, series = plot["x"], plot["y"], plot.get("series")
    fields = [x["field"], y["field"]] + ([series["field"]] if series else [])
    if len(set(fields)) != len(fields):
        out.add("plot.field.duplicate")
    distinct: set = set()
    for row in plot.get("data") or []:
        if any(k not in fields for k in row):
            out.add("plot.field.unknown")
        xv = row.get(x["field"])
        if xv is None or y["field"] not in row:
            out.add("plot.row.missing")
        elif x["type"] == "quantitative" and not _num(xv):
            out.add("plot.type")
        elif x["type"] == "temporal" and not (isinstance(xv, str) and TEMPORAL.match(xv)):
            out.add("plot.temporal")
        yv = row.get(y["field"])
        if yv is not None and not _num(yv):
            out.add("plot.type")
        if series:
            sv = row.get(series["field"])
            if sv is None:
                out.add("plot.row.missing")
            else:
                distinct.add(sv)
    if len(distinct) > caps()["maxSeries"]:
        out.add("plot.series.max")
    return out


def check_plot(plot, header: bool = False) -> list[str]:
    """Rule ids violated by ``plot`` (sorted, unique). ``[]`` means valid. Never raises."""
    rules = {_rule(e) for e in _validator(header).iter_errors(plot)}
    if isinstance(plot, dict) and _nonfinite(plot.get("data")):
        rules.add("plot.number.finite")
    if rules:
        return sorted(rules)
    return sorted(_semantic(plot))


_D3 = {"currency": "$,.2f", "percent": ".1%", "integer": ",d", "number": ",.2~f", "date": "%b %-d, %Y", "month": "%b %Y"}


def to_vega_lite(plot: dict) -> dict:
    """The Vega-Lite spec for a valid ``plot`` (data by name ``result``; values travel in ``ChartPart.data``).

    Mirrors ``plotToVegaLite`` in web/src/contract/plot.ts; ``api/tests/test_plot_118.py`` pins both to the examples."""
    x, y, series = plot["x"], plot["y"], plot.get("series")
    fields = [x["field"], y["field"]] + ([series["field"]] if series else [])
    xenc: dict = {"field": x["field"], "type": x["type"], "title": x["title"]}
    if x.get("format"):
        xenc["axis"] = {"format": _D3[x["format"]]}
    yenc: dict = {"field": y["field"], "type": "quantitative", "title": y["title"]}
    if y.get("format"):
        yenc["axis"] = {"format": _D3[y["format"]]}
    enc: dict = {"x": xenc, "y": yenc,
                 "tooltip": [{"field": f, "type": "quantitative" if f == y["field"] else
                              ("temporal" if f == x["field"] and x["type"] == "temporal" else
                               "quantitative" if f == x["field"] and x["type"] == "quantitative" else "nominal")}
                             for f in fields]}
    sort = plot.get("sort", "-y" if plot["kind"] == "bar" else "none")
    if sort == "-y":
        xenc["sort"] = "-y"
    elif sort == "x":
        xenc["sort"] = "ascending"
    if series:
        enc["color"] = {"field": series["field"], "type": "nominal", "title": series["title"]}
    mark = {"type": plot["kind"], "point": plot["kind"] == "line", "tooltip": True}
    return {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "data": {"name": "result"},
            "mark": mark, "encoding": enc, "width": "container", "height": 280}


def header_of(plot: dict) -> dict:
    return {k: v for k, v in plot.items() if k != "data"}


def table_from_plot(chart: dict) -> dict | None:
    """Fallback when an invalid chart has no table in its message: its points as a plain table, if they are rows."""
    from api.ids import new_id
    plot = chart.get("plot") if isinstance(chart.get("plot"), dict) else {}
    data = plot.get("data") if isinstance(plot.get("data"), list) else (chart.get("data") or {}).get("values")
    if not isinstance(data, list) or not data or not all(isinstance(r, dict) for r in data):
        return None
    keys: list[str] = []
    for r in data[: caps()["maxPoints"]]:
        keys += [k for k in r if isinstance(k, str) and k not in keys]
    keys = keys[:20]

    def cell(v):
        if isinstance(v, bool) or v is None or isinstance(v, str):
            return v if not isinstance(v, str) else v[:200]
        if isinstance(v, (int, float)):
            return v if math.isfinite(v) else None
        return None

    rows = [[cell(r.get(k)) for k in keys] for r in data[: caps()["maxPoints"]]]
    cols = [{"key": k, "label": k.replace("_", " ").capitalize(),
             "type": "number" if all(isinstance(row[i], (int, float)) and not isinstance(row[i], bool) or row[i] is None
                                     for row in rows) else "string"} for i, k in enumerate(keys)]
    table = {"type": "table", "id": chart.get("id") or new_id("p_"), "columns": cols, "rows": rows,
             "rowCount": len(rows), "title": str(chart.get("title") or "Chart data")[:120]}
    if chart.get("sensitivity"):
        table["sensitivity"] = chart["sensitivity"]
    return table


def _chart_ok(chart: dict) -> list[str]:
    if "plot" not in chart:
        return ["plot.required"]
    return check_plot(chart["plot"])


def gate_parts(parts: list[dict]) -> tuple[list[dict], list[list[str]]]:
    """Validate every chart (top level and inside a chart artifact) before it is emitted.

    An invalid chart is dropped. If the message has no table with the chart's ``tablePartId``, its points are kept
    as a table instead (when they are plain rows). Returns ``(parts, rejected)``: one rule-id list per dropped chart,
    for the audit log (rule ids only, never values)."""
    table_ids = {p.get("id") for p in parts if isinstance(p, dict) and p.get("type") == "table"}
    out: list[dict] = []
    rejected: list[list[str]] = []
    for p in parts:
        chart = p if p.get("type") == "chart" else (
            p.get("content") if p.get("type") == "artifact" and isinstance(p.get("content"), dict)
            and p["content"].get("type") == "chart" else None)
        if chart is None:
            out.append(p)
            continue
        bad = _chart_ok(chart)
        if not bad:
            out.append(p)
            continue
        rejected.append(bad)
        if chart.get("tablePartId") not in table_ids:
            fallback = table_from_plot(chart)
            if fallback is not None:
                if p is chart:
                    out.append(fallback)
                else:
                    out.append({**p, "kind": "table", "content": fallback})
    return out, rejected
