"""Write api/contract/plot_cases.json: the PlotSpec validation cases both validators run (contract 1.18.0).

    python -m api.contract.make_plot_cases          # write
    python -m api.contract.make_plot_cases --check  # exit 1 on drift

JSON cannot hold NaN/Infinity or huge strings compactly, so the file uses two sentinels that each loader expands:
``{"$nonfinite": "NaN" | "Infinity" | "-Infinity"}`` and ``{"$repeat": [text, n]}`` (``text`` repeated ``n`` times).
``{"$range": [n, x, y]}`` builds n points ``{x: "P<i>", y: i}``. Each case: ``{name, header?, plot, rules}``.
"""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

OUT = Path(__file__).with_name("plot_cases.json")

NAN, INF, NINF = ({"$nonfinite": "NaN"}, {"$nonfinite": "Infinity"}, {"$nonfinite": "-Infinity"})


def rep(text: str, n: int) -> dict:
    return {"$repeat": [text, n]}


BAR = {"v": 1, "kind": "bar", "title": "Paid amount by payer",
       "x": {"field": "payer", "type": "nominal", "title": "Payer"},
       "y": {"field": "paid_amount", "type": "quantitative", "title": "Paid amount", "format": "currency"},
       "sort": "-y",
       "data": [{"payer": "Aetna", "paid_amount": 1200.5}, {"payer": "Cigna", "paid_amount": 980}]}
LINE = {"v": 1, "kind": "line", "title": "Denial rate by month",
        "x": {"field": "month", "type": "temporal", "title": "Month", "format": "month"},
        "y": {"field": "denial_rate", "type": "quantitative", "title": "Denial rate", "format": "percent"},
        "series": {"field": "payer", "title": "Payer"},
        "data": [{"month": "2026-01-01", "denial_rate": 0.12, "payer": "Aetna"},
                 {"month": "2026-02-01", "denial_rate": None, "payer": "Aetna"},
                 {"month": "2026-01-01", "denial_rate": 0.1, "payer": "Cigna"}]}


def edit(base: dict, fn) -> dict:
    p = copy.deepcopy(base)
    fn(p)
    return p


def _set(path: str, value):
    def fn(p):
        cur = p
        keys = path.split(".")
        for k in keys[:-1]:
            cur = cur[int(k)] if isinstance(cur, list) else cur[k]
        last = keys[-1]
        if isinstance(cur, list):
            cur[int(last)] = value
        else:
            cur[last] = value
    return fn


def _del(path: str):
    def fn(p):
        cur = p
        keys = path.split(".")
        for k in keys[:-1]:
            cur = cur[int(k)] if isinstance(cur, list) else cur[k]
        del cur[keys[-1]]
    return fn


def bar_rows(n: int) -> dict:
    """``{"$range": [n, xField, yField]}`` expands to ``[{xField: "P0", yField: 0}, …]`` (n rows)."""
    return {"$range": [n, "payer", "paid_amount"]}


def cases() -> list[dict]:
    c: list[tuple[str, dict, list[str]]] = [
        ("valid bar", BAR, []),
        ("valid line with series and a null gap", LINE, []),
        ("valid area, integer x quantitative, no format", edit(BAR, lambda p: p.update(
            kind="area", x={"field": "bucket", "type": "quantitative", "title": ""},
            data=[{"bucket": 1, "paid_amount": 3}, {"bucket": 2.5, "paid_amount": -1e300}]) or p["y"].pop("format")), []),
        ("valid point, ordinal x with numbers", edit(BAR, lambda p: p.update(
            kind="point", x={"field": "n", "type": "ordinal", "title": "N"}, data=[{"n": 3, "paid_amount": 0}])), []),
        ("valid at the point cap", edit(BAR, _set("data", bar_rows(2000))), []),
        ("valid at 10 series", edit(LINE, _set("data", [{"month": "2026-01", "denial_rate": 0.1, "payer": f"S{i}"}
                                                       for i in range(10)])), []),
        ("valid temporal forms", edit(LINE, _set("data", [
            {"month": m, "denial_rate": 1, "payer": "A"} for m in
            ("2026-01", "2026-01-31", "2026-01-31T10:00", "2026-01-31 10:00:59", "2026-01-31T10:00:59.123Z",
             "2026-01-31T10:00:59+05:30", "2026-01-31T10:00-0800")])), []),
        ("valid title and labels at their caps", edit(BAR, lambda p: (
            p.update(title=rep("t", 120)), p["x"].update(title=rep("x", 80)),
            p["data"][0].update(payer=rep("é", 200)))), []),
        ("valid 64-char field name", edit(BAR, lambda p: (
            p["x"].update(field="f" * 64), [r.update({"f" * 64: r.pop("payer")}) for r in p["data"]])), []),
        ("valid sorts", edit(BAR, _set("sort", "none")), []),
        ("valid v written as 1.0", edit(BAR, _set("v", 1.0)), []),
        ("valid 120 emoji title (code points, not UTF-16 units)", edit(BAR, _set("title", rep("📊", 120))), []),
        # structure
        ("not an object", [], ["plot.type"]),
        ("null", None, ["plot.type"]),
        ("unknown top-level field", edit(BAR, _set("$schema", "x")), ["plot.closed"]),
        ("vega-lite smuggled in", edit(BAR, _set("encoding", {})), ["plot.closed"]),
        ("unknown axis field", edit(BAR, _set("x.axis", {"format": "x"})), ["plot.closed"]),
        ("unknown series field", edit(LINE, _set("series.color", "red")), ["plot.closed"]),
        ("missing v", edit(BAR, _del("v")), ["plot.required"]),
        ("missing title", edit(BAR, _del("title")), ["plot.required"]),
        ("missing data", edit(BAR, _del("data")), ["plot.required"]),
        ("missing y.field", edit(BAR, _del("y.field")), ["plot.required"]),
        ("missing series.title", edit(LINE, _del("series.title")), ["plot.required"]),
        ("v 2", edit(BAR, _set("v", 2)), ["plot.version"]),
        ("v as string", edit(BAR, _set("v", "1")), ["plot.version"]),
        ("unknown kind", edit(BAR, _set("kind", "pie")), ["plot.kind"]),
        ("kind casing", edit(BAR, _set("kind", "Bar")), ["plot.kind"]),
        ("unknown x type", edit(BAR, _set("x.type", "geojson")), ["plot.enum"]),
        ("y nominal", edit(BAR, _set("y.type", "nominal")), ["plot.enum"]),
        ("unknown format", edit(BAR, _set("y.format", "$,.2f")), ["plot.enum"]),
        ("unknown sort", edit(BAR, _set("sort", "y")), ["plot.enum"]),
        ("title too long", edit(BAR, _set("title", rep("t", 121))), ["plot.label.length"]),
        ("empty title", edit(BAR, _set("title", "")), ["plot.label.length"]),
        ("121 emoji title", edit(BAR, _set("title", rep("📊", 121))), ["plot.label.length"]),
        ("axis title too long", edit(BAR, _set("y.title", rep("y", 81))), ["plot.label.length"]),
        ("series title too long", edit(LINE, _set("series.title", rep("s", 81))), ["plot.label.length"]),
        ("category too long", edit(BAR, _set("data.0.payer", rep("p", 201))), ["plot.label.length"]),
        ("field with a space", edit(BAR, _set("x.field", "payer name")), ["plot.field.name"]),
        ("field with a dot (vega path injection)", edit(BAR, _set("x.field", "a.b")), ["plot.field.name"]),
        ("field starting with a digit", edit(BAR, _set("x.field", "1x")), ["plot.field.name"]),
        ("field with a trailing newline", edit(BAR, _set("x.field", "payer\n")), ["plot.field.name"]),
        ("field of 65 chars", edit(BAR, _set("x.field", "f" * 65)), ["plot.field.name"]),
        ("row key not an identifier", edit(BAR, _set("data.0", {"payer": "A", "paid_amount": 1, "bad key": 1})),
         ["plot.field.name"]),
        ("too many points", edit(BAR, _set("data", bar_rows(2001))), ["plot.points.max"]),
        ("no points", edit(BAR, _set("data", [])), ["plot.points.min"]),
        ("data not an array", edit(BAR, _set("data", {"payer": "A"})), ["plot.type"]),
        ("row not an object", edit(BAR, _set("data.1", ["Cigna", 980])), ["plot.type"]),
        ("boolean value", edit(BAR, _set("data.0.paid_amount", True)), ["plot.type"]),
        ("object value", edit(BAR, _set("data.0.paid_amount", {"v": 1})), ["plot.type"]),
        ("title not a string", edit(BAR, _set("title", 5)), ["plot.type"]),
        ("NaN y", edit(BAR, _set("data.0.paid_amount", NAN)), ["plot.number.finite"]),
        ("Infinity y", edit(BAR, _set("data.0.paid_amount", INF)), ["plot.number.finite"]),
        ("-Infinity quantitative x", edit(BAR, lambda p: (p["x"].update(type="quantitative"),
                                                          p["data"][0].update(payer=NINF))), ["plot.number.finite"]),
        ("several structural faults at once", edit(BAR, lambda p: (p.update(kind="pie", extra=1), p.pop("title"))),
         ["plot.closed", "plot.kind", "plot.required"]),
        ("structural faults hide semantic ones", edit(BAR, lambda p: (p.update(kind="pie"),
                                                                     p["data"][0].update(other=1))), ["plot.kind"]),
        # semantics (only when the structure is valid)
        ("x and y name the same field", edit(BAR, _set("y.field", "payer")), ["plot.field.duplicate", "plot.field.unknown", "plot.type"]),
        ("series repeats x", edit(LINE, _set("series.field", "month")),
         ["plot.field.duplicate", "plot.field.unknown"]),
        ("row with an undeclared key", edit(BAR, _set("data.0.member_id", "M1")), ["plot.field.unknown"]),
        ("row without x", edit(BAR, _del("data.0.payer")), ["plot.row.missing"]),
        ("row with null x", edit(BAR, _set("data.0.payer", None)), ["plot.row.missing"]),
        ("row without y", edit(BAR, _del("data.0.paid_amount")), ["plot.row.missing"]),
        ("row without series", edit(LINE, _del("data.0.payer")), ["plot.row.missing"]),
        ("y as a string", edit(BAR, _set("data.0.paid_amount", "1200")), ["plot.type"]),
        ("quantitative x as a string", edit(BAR, _set("x.type", "quantitative")), ["plot.type"]),
        ("11 series", edit(LINE, _set("data", [{"month": "2026-01", "denial_rate": 0.1, "payer": f"S{i}"}
                                                for i in range(11)])), ["plot.series.max"]),
        ("temporal x not a date", edit(LINE, _set("data.0.month", "January")), ["plot.temporal"]),
        ("temporal x as a number", edit(LINE, _set("data.0.month", 20260101)), ["plot.temporal"]),
        ("temporal x with non-ASCII digits", edit(LINE, _set("data.0.month", "٢٠٢٦-01-01")), ["plot.temporal"]),
        ("temporal x with a trailing newline", edit(LINE, _set("data.0.month", "2026-01-01\n")), ["plot.temporal"]),
        ("__proto__ row key with a number (an unknown field, never prototype pollution)",
         edit(BAR, lambda p: p["data"][0].update({"__proto__": 1})), ["plot.field.unknown"]),
        ("__proto__ row key with an object", edit(BAR, lambda p: p["data"][0].update({"__proto__": {"polluted": 1}})),
         ["plot.type"]),
        ("constructor row key", edit(BAR, lambda p: p["data"][0].update({"constructor": "x"})), ["plot.field.unknown"]),
        ("several semantic faults", edit(LINE, lambda p: (p["data"][0].update(month="Jan", extra=1),
                                                         p["data"][1].pop("payer"))),
         ["plot.field.unknown", "plot.row.missing", "plot.temporal"]),
    ]
    out = [{"name": n, "plot": p, "rules": sorted(r)} for n, p, r in c]
    # chart.start headers: the same schema without data
    hdr = {k: v for k, v in LINE.items() if k != "data"}
    out += [
        {"name": "header: valid", "header": True, "plot": hdr, "rules": []},
        {"name": "header: data is not allowed", "header": True, "plot": LINE, "rules": ["plot.closed"]},
        {"name": "header: missing kind", "header": True, "plot": {k: v for k, v in hdr.items() if k != "kind"},
         "rules": ["plot.required"]},
        {"name": "header: duplicate fields", "header": True,
         "plot": {**hdr, "series": {"field": "month", "title": "M"}}, "rules": ["plot.field.duplicate"]},
    ]
    names = [x["name"] for x in out]
    assert len(names) == len(set(names)), "case names must be unique"
    return out


def render() -> str:
    return json.dumps({"version": 1, "description": "PlotSpec validation cases (contract 1.18.0). Generated by "
                       "`python -m api.contract.make_plot_cases`; run by api/tests/test_plot_118.py and "
                       "web/src/contract/plot.test.ts. Both must report exactly `rules`.", "cases": cases()},
                      indent=1, ensure_ascii=False) + "\n"


def expand(v):
    """Replace the sentinels (shared semantics with the TS loader)."""
    if isinstance(v, dict):
        if set(v) == {"$nonfinite"}:
            return float({"NaN": "nan", "Infinity": "inf", "-Infinity": "-inf"}[v["$nonfinite"]])
        if set(v) == {"$range"}:
            n, xf, yf = v["$range"]
            return [{xf: f"P{i}", yf: i} for i in range(n)]
        if set(v) == {"$repeat"}:
            text, n = v["$repeat"]
            return text * n
        return {k: expand(x) for k, x in v.items()}
    if isinstance(v, list):
        return [expand(x) for x in v]
    return v


def load() -> list[dict]:
    return [{**c, "plot": expand(c["plot"])} for c in json.loads(OUT.read_text())["cases"]]


def main(argv: list[str]) -> int:
    text = render()
    if "--check" in argv:
        if (OUT.read_text() if OUT.exists() else "") != text:
            print("api/contract/plot_cases.json is out of date; run: python -m api.contract.make_plot_cases",
                  file=sys.stderr)
            return 1
        return 0
    OUT.write_text(text)
    print(f"wrote {OUT.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
