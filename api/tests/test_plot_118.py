"""1.18.0 strict PlotSpec (CONTRACT.md §16.1–16.2): one closed, versioned schema; the server validates before emitting
(an invalid chart is dropped and the table stays); charts stream as chart.start + chart.points + part; the shared cases
in api/contract/plot_cases.json give the same rule ids here and in web/src/contract/plot.test.ts."""
from __future__ import annotations

import json
import math
import threading
from decimal import Decimal

import pytest

from api import mapping, plot as P
from api.contract import CONTRACT_VERSION, make_plot_cases, schema
from api.contract.conformance import check
from api.stream_parts import CHART_CHUNK_POINTS, emit_parts
from api.tests.conftest import ask, parts as parts_of

CASES = make_plot_cases.load()
D = schema()["$defs"]
PLOT_DEFS = ["PlotSpec", "PlotHeader", "PlotAxisX", "PlotAxisY", "PlotSeries"]


def test_version_and_cases_are_current():
    assert tuple(map(int, CONTRACT_VERSION.split("."))) >= (1, 18, 0)
    assert make_plot_cases.main(["--check"]) == 0


@pytest.mark.parametrize("case", CASES, ids=[c["name"] for c in CASES])
def test_shared_case(case):
    assert P.check_plot(case["plot"], header=case.get("header", False)) == case["rules"]


def test_every_rule_has_a_failing_case_and_cases_cover_valid_inputs():
    seen = {r for c in CASES for r in c["rules"]}
    assert seen == set(P.RULES)
    assert sum(1 for c in CASES if not c["rules"]) >= 10


def test_schema_is_closed_and_caps_agree_with_the_keywords():
    for name in PLOT_DEFS:
        assert D[name]["additionalProperties"] is False, name
    caps = D["PlotSpec"]["x-caps"]
    assert D["PlotSpec"]["properties"]["data"]["maxItems"] == caps["maxPoints"]
    assert D["PlotSpec"]["properties"]["data"]["minItems"] == caps["minPoints"]
    assert D["PlotSpec"]["properties"]["title"]["maxLength"] == caps["maxTitle"]
    for ax in ("PlotAxisX", "PlotAxisY", "PlotSeries"):
        assert D[ax]["properties"]["title"]["maxLength"] == caps["maxAxisTitle"]
        assert D[ax]["properties"]["field"]["pattern"] == D["PlotRow"]["propertyNames"]["pattern"]
    assert D["PlotValue"]["maxLength"] == caps["maxString"]
    assert D["PlotHeader"]["properties"] == {k: v for k, v in D["PlotSpec"]["properties"].items() if k != "data"}
    assert D["ChartPart"]["properties"]["plot"]["$ref"] == "#/$defs/PlotSpec"
    assert D["ChartStart"]["properties"]["plot"]["$ref"] == "#/$defs/PlotHeader"


def test_check_plot_is_total():
    class Weird:
        pass
    for x in (None, 1, "s", [], {}, {"v": 1, "data": [{"a": Weird()}]}, {"v": float("nan")}, {"data": [float("inf")]}):
        assert P.check_plot(x)


COLS = [{"key": "month", "label": "Month", "type": "date"},
        {"key": "paid_amount", "label": "Paid amount", "type": "currency", "format": "$,.2f"},
        {"key": "payer", "label": "Payer", "type": "string"}]


def test_chart_for_emits_a_valid_plot_and_derives_the_spec_from_it():
    rows = [["2026-01-01", Decimal("1.5"), "A"], ["2026-02-01", 2.5, "B"], ["2026-03-01", 3, "A"]]
    c = mapping.chart_for(COLS, rows)
    assert P.check_plot(c["plot"]) == []
    assert c["spec"] == P.to_vega_lite(c["plot"])
    assert c["data"]["values"] == c["plot"]["data"]
    assert c["plot"]["kind"] == "line" and c["plot"]["y"]["format"] == "currency"
    assert c["plot"]["series"] == {"field": "payer", "title": "Payer"}


@pytest.mark.parametrize("bad,why", [
    ([["2026-01-01", 1.5, "A" * 201], ["2026-02-01", 2.5, "B"]], "category over 200 chars"),
    ([["2026-01-01", float("nan"), "A"], ["2026-02-01", 2.5, "B"]], "NaN"),
    ([["2026-01-01", Decimal("Infinity"), "A"], ["2026-02-01", 2.5, "B"]], "Decimal infinity"),
    ([["January", 1.5, "A"], ["February", 2.5, "B"]], "date column with non-ISO text"),
    ([["2026-01-01", "lots", "A"], ["2026-02-01", 2.5, "B"]], "a non-numeric measure"),
])
def test_chart_for_drops_an_invalid_plot(bad, why):
    assert mapping.chart_for(COLS, bad) is None, why


def test_chart_for_long_labels_are_truncated_not_rejected():
    cols = [{"key": "payer", "label": "P" * 300, "type": "string"},
            {"key": "denial_rate", "label": "R" * 300, "type": "percent", "format": ".1%"}]
    c = mapping.chart_for(cols, [["A", 0.1], ["B", 0.2]])
    assert P.check_plot(c["plot"]) == [] and len(c["plot"]["title"]) == 120 and len(c["plot"]["x"]["title"]) == 80


def _chart(pid="c1", table="t1", **plot):
    p = {"v": 1, "kind": "bar", "title": "Paid by payer", "x": {"field": "payer", "type": "nominal", "title": "Payer"},
         "y": {"field": "paid", "type": "quantitative", "title": "Paid"},
         "data": [{"payer": "A", "paid": 1}, {"payer": "B", "paid": 2}], **plot}
    out = {"type": "chart", "id": pid, "library": "vega-lite", "spec": {}, "alt": "a", "plot": p}
    if table:
        out["tablePartId"] = table
    return out


TABLE = {"type": "table", "id": "t1", "columns": [], "rows": [], "rowCount": 0}


def test_gate_keeps_valid_charts():
    parts = [TABLE, _chart()]
    assert P.gate_parts(parts) == (parts, [])


def test_gate_drops_an_invalid_chart_and_keeps_the_table():
    out, rejected = P.gate_parts([TABLE, _chart(kind="pie")])
    assert out == [TABLE] and rejected == [["plot.kind"]]


def test_gate_falls_back_to_a_table_when_the_message_has_none():
    out, rejected = P.gate_parts([_chart(table=None, kind="pie")])
    assert rejected == [["plot.kind"]]
    assert out[0]["type"] == "table" and out[0]["id"] == "c1"
    assert out[0]["columns"][0]["key"] == "payer" and out[0]["columns"][1]["type"] == "number"
    assert out[0]["rows"] == [["A", 1], ["B", 2]] and out[0]["rowCount"] == 2


def test_gate_fallback_table_scrubs_non_finite_and_odd_values():
    bad = _chart(table=None, data=[{"payer": "A", "paid": float("nan")}, {"payer": {"x": 1}, "paid": 2}])
    out, rejected = P.gate_parts([bad])
    assert rejected and out[0]["rows"] == [["A", None], [None, 2]]
    json.dumps(out, allow_nan=False)


def test_gate_drops_a_chart_without_plot_and_artifact_charts():
    legacy = {"type": "chart", "id": "c9", "library": "vega-lite", "spec": {}, "alt": "a"}
    art = {"type": "artifact", "id": "a1", "artifactId": "af1", "version": 1, "kind": "chart", "title": "t",
           "content": _chart(kind="pie")}
    out, rejected = P.gate_parts([TABLE, legacy, art])
    assert out == [TABLE] and rejected == [["plot.required"], ["plot.kind"]]
    ok_art = {**art, "content": _chart()}
    assert P.gate_parts([TABLE, ok_art])[0] == [TABLE, ok_art]
    art_no_table = {**art, "content": _chart(table=None, kind="pie")}
    out, _ = P.gate_parts([art_no_table])
    assert out[0]["kind"] == "table" and out[0]["content"]["type"] == "table"


def _stream(parts, cancel_after=None):
    evs = [{"type": "message.start", "messageId": "m", "conversationId": "c", "contractVersion": CONTRACT_VERSION}]
    stop = threading.Event()

    def emit(e):
        evs.append(e)
        if cancel_after and sum(x["type"] == "chart.points" for x in evs) >= cancel_after:
            stop.set()
    ok = emit_parts(parts, emit, cancelled=stop)
    evs.append({"type": "message.end", "messageId": "m", "finishReason": "stop" if ok else "aborted", "usage": {}})
    return [{**e, "seq": i} for i, e in enumerate(evs)]


def test_charts_stream_progressively_and_conform():
    data = [{"payer": f"P{i}", "paid": i} for i in range(120)]
    evs = _stream([{**TABLE, "rows": [["x"]], "rowCount": 1}, _chart(data=data)])
    assert check(evs) == []
    starts = [e for e in evs if e["type"] == "chart.start"]
    points = [e for e in evs if e["type"] == "chart.points"]
    assert len(starts) == 1 and "data" not in starts[0]["plot"] and starts[0]["tablePartId"] == "t1"
    assert P.check_plot(starts[0]["plot"], header=True) == []
    assert [p["offset"] for p in points] == list(range(0, 120, CHART_CHUNK_POINTS))
    assert sum(len(p["rows"]) for p in points) == 120
    assert evs.index(starts[0]) < evs.index(points[0]) < [i for i, e in enumerate(evs) if e["type"] == "part"][-1]


def test_cancel_mid_chart_still_conforms():
    data = [{"payer": f"P{i}", "paid": i} for i in range(200)]
    evs = _stream([{**TABLE, "rows": [["x"]], "rowCount": 1}, _chart(data=data)], cancel_after=1)
    assert evs[-1]["finishReason"] == "aborted" and check(evs) == []


def test_demo_chart_answer_streams_a_valid_plot(client):
    _, evs = ask(client, "Paid amount trend by month")
    assert check(evs) == []
    charts = [p for p in parts_of(evs) if p["type"] == "chart"]
    assert charts and all(P.check_plot(c["plot"]) == [] for c in charts)
    assert any(e["type"] == "chart.start" for e in evs)


def test_server_drops_an_invalid_chart_before_emitting_and_audits_rule_ids_only(app, client, monkeypatch):
    real = mapping.chart_for

    def broken(cols, rows):
        c = real(cols, rows)
        if c:
            c["plot"]["data"][0][c["plot"]["y"]["field"]] = float("nan")
            c["plot"]["title"] = "Secret title " * 20
        return c
    monkeypatch.setattr(mapping, "chart_for", broken)
    _, evs = ask(client, "Paid amount trend by month")
    assert check(evs) == []
    assert not [p for p in parts_of(evs) if p["type"] == "chart"]
    assert not [e for e in evs if e["type"].startswith("chart.")]
    assert [p for p in parts_of(evs) if p["type"] == "table"]  # the table stays
    dropped = [e for e in app.state.audit.events if e["event"] == "chart_dropped"]
    assert dropped and dropped[0]["rules"] == "plot.label.length,plot.number.finite"
    assert "Secret" not in str(app.state.audit.events)
    for line in (json.dumps(e) for e in evs):
        assert "NaN" not in line


def test_to_vega_lite_variants():
    p = {"v": 1, "kind": "point", "title": "t", "x": {"field": "n", "type": "quantitative", "title": "N", "format": "integer"},
         "y": {"field": "v", "type": "quantitative", "title": "V", "format": "number"}, "sort": "x", "data": [{"n": 1, "v": 1}]}
    s = P.to_vega_lite(p)
    assert s["encoding"]["x"] == {"field": "n", "type": "quantitative", "title": "N", "axis": {"format": ",d"},
                                  "sort": "ascending"}
    assert [t["type"] for t in s["encoding"]["tooltip"]] == ["quantitative", "quantitative"]
    assert s["mark"] == {"type": "point", "point": False, "tooltip": True}
    assert "sort" not in P.to_vega_lite({**p, "sort": "none"})["encoding"]["x"]
    assert math.isfinite(s["height"])
