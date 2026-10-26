"""Map an ``AnalyzerAnswer`` to contract parts (web/src/lib/contract.ts, spec section 5).

Order: tool-call steps, the answer as markdown text, the SQL that produced it, the result
table (preview capped, full result behind an opaque download id), a file chip for large
results, a Vega-Lite chart when the result is a time series or categorical breakdown,
assumptions, then caveats (warnings and PHI redaction).
"""
from __future__ import annotations

import datetime as _dt
import re
from decimal import Decimal

from analyzer.answer import AnalyzerAnswer
from analyzer.tools import is_phi_column

from api.ids import new_id
from api.store import DownloadRegistry, Principal

PREVIEW_ROWS = 50
CHART_MAX_ROWS = 5000

_TOOL_LABELS = {
    "run_sql": "Ran a query",
    "list_relations": "Listed views",
    "describe_relation": "Described {relation}",
    "sample_rows": "Sampled {relation}",
    "profile": "Profiled data",
    "lookup_dictionary": "Read the data dictionary",
    "lookup_glossary": "Read the glossary",
    "query_metrics": "Queried metrics",
    "lookup_values": "Looked up values",
    "lookup_examples": "Searched examples",
    "request_escalation": "Requested escalation",
    "final_answer": "Checked the final query",
    "ask": "Asked for clarification",
    "refuse": "Declined",
}
_TOOL_KIND = {"list_relations": "list_views", "describe_relation": "describe_view", "lookup_examples": "search_examples"}
_MONEY = re.compile(r"(amount|amt|paid|charge|billed|allowed|balance|revenue|payment|_usd$)", re.I)
_PCT = re.compile(r"(rate|pct|percent|share|ratio)", re.I)
_TIME = re.compile(r"(date|month|week|day|year|quarter|period)", re.I)


def column_type(name: str, values: list) -> str:
    sample = next((v for v in values if v is not None), None)
    if isinstance(sample, bool):
        return "boolean"
    if isinstance(sample, _dt.datetime):
        return "datetime"
    if isinstance(sample, _dt.date):
        return "date"
    if isinstance(sample, int):
        return "currency" if _MONEY.search(name) else "integer"
    if isinstance(sample, (float, Decimal)):
        if _MONEY.search(name):
            return "currency"
        if _PCT.search(name):
            return "percent"
        return "number"
    if isinstance(sample, str):
        if _TIME.search(name) and re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", sample):
            return "date"
        if re.search(r"(code|carc|rarc|cpt|icd|hcpcs)", name, re.I):
            return "code"
    return "string"


#: Revenue-cycle acronyms kept upper-case in labels and chart titles (QA-P5: "Claims by CARC code", not "carc code").
ACRONYMS = frozenset({"carc", "rarc", "bcbs", "cpt", "npi", "icd", "hcpcs", "drg", "eob", "era", "pos", "hmo", "ppo",
                      "ar", "id", "mrn", "tin", "ein", "ndc", "loinc", "cms", "uhc"})


def _word(w: str, first: bool) -> str:
    if w.lower() in ACRONYMS:
        return w.upper()
    return w.capitalize() if first else w.lower()


def humanize(name: str) -> str:
    """``carc_code`` -> ``CARC code``; ``payer_name`` -> ``Payer name``."""
    words = [w for w in str(name).replace("_", " ").split() if w]
    return " ".join(_word(w, i == 0) for i, w in enumerate(words)) or str(name)


def lower_label(label: str) -> str:
    """Mid-sentence form of a label: lower-case except acronyms (``CARC code``, ``service month``)."""
    return " ".join(w if w.lower() in ACRONYMS and w.isupper() else w.lower() for w in label.split())


def columns_for(names: list, rows: list) -> list[dict]:
    out = []
    for i, name in enumerate(names):
        ctype = column_type(name, [r[i] for r in rows[:200]])
        col = {"key": name, "label": humanize(name), "type": ctype}
        if ctype == "currency":
            col["format"] = "$,.2f"
        elif ctype == "percent":
            col["format"] = ".1%"
        if is_phi_column(name):
            col["phi"] = True
        out.append(col)
    return out


def _jsonable(v):
    if isinstance(v, Decimal):
        return float(v)
    if isinstance(v, (_dt.date, _dt.datetime)):
        return v.isoformat()
    return v


def tool_parts(trace: list) -> list[dict]:
    """One ``tool_call`` part per trace step. Arguments carry SQL text and relation names only, never rows."""
    parts = []
    for step in trace:
        tool = str(step.get("tool") or "tool")
        args = dict(step.get("arguments") or {})
        label = _TOOL_LABELS.get(tool, tool.replace("_", " ").capitalize())
        try:
            label = label.format(relation=args.get("relation", "a view"))
        except (KeyError, IndexError):
            pass
        part = {"type": "tool_call", "id": new_id("p_"), "tool": _TOOL_KIND.get(tool, tool), "label": label,
                "state": "ok" if step.get("ok") else "error", "agent": "analyst"}
        sql = args.pop("sql", None)
        if sql or args:
            part["input"] = {}
            if sql:
                part["input"]["sql"] = str(sql)
            if args:
                part["input"]["args"] = {k: v for k, v in args.items() if isinstance(v, (str, int, float, bool))}
        if not step.get("ok"):
            part["output"] = {"message": str(step.get("summary") or "")[:300]}
        parts.append(part)
    return parts


def chart_for(columns: list[dict], rows: list) -> dict | None:
    """Vega-Lite for a time series (line) or a categorical breakdown (bar). ``None`` otherwise."""
    if not rows or len(rows) < 2 or len(rows) > CHART_MAX_ROWS or len(columns) < 2:
        return None
    if any(re.search(r"(_key|_id|_number)$", c["key"]) for c in columns):
        return None  # row-level listing, not an aggregate
    usable = [c for c in columns if not c.get("phi")]
    measures = [c for c in usable if c["type"] in ("integer", "number", "currency", "percent")]
    temporal = [c for c in usable if c["type"] in ("date", "datetime")]
    categorical = [c for c in usable if c["type"] in ("string", "code")]
    if not measures:
        return None
    # The headline measure comes first in the SELECT (e.g. denial_rate, then claims as support);
    # a rate/percent beats a raw count when both are present.
    rates = [c for c in measures if c["type"] == "percent" or re.search(r"(rate|pct|percent|share)", c["key"])]
    y = rates[0] if rates else measures[0]
    keys = [c["key"] for c in columns]
    if temporal and len(rows) <= 500:
        x, mark, xtype = temporal[0], "line", "temporal"
    elif categorical and len(rows) <= 50:
        x, mark, xtype = categorical[0], "bar", "nominal"
    else:
        return None
    color = None
    if xtype == "temporal" and categorical:
        distinct = {r[keys.index(categorical[0]["key"])] for r in rows}
        if 1 < len(distinct) <= 10:
            color = categorical[0]
    fields = [x["key"], y["key"]] + ([color["key"]] if color else [])
    values = [{f: _jsonable(r[keys.index(f)]) for f in fields} for r in rows]
    yaxis = {"field": y["key"], "type": "quantitative", "title": y["label"]}
    if y.get("format"):
        yaxis["axis"] = {"format": y["format"]}
    encoding = {"x": {"field": x["key"], "type": xtype, "title": x["label"]}, "y": yaxis,
                "tooltip": [{"field": f, "type": "quantitative" if f == y["key"] else
                             ("temporal" if f == x["key"] and xtype == "temporal" else "nominal")} for f in fields]}
    if mark == "bar":
        encoding["x"]["sort"] = "-y"
    if color:
        encoding["color"] = {"field": color["key"], "type": "nominal", "title": color["label"]}
    title = f"{y['label']} by {lower_label(x['label'])}"
    spec = {"$schema": "https://vega.github.io/schema/vega-lite/v5.json", "data": {"name": "result"},
            "mark": {"type": mark, "point": mark == "line", "tooltip": True}, "encoding": encoding,
            "width": "container", "height": 280}
    return {"type": "chart", "id": new_id("p_"), "library": "vega-lite", "spec": spec,
            "data": {"name": "result", "values": values}, "title": title,
            "alt": f"{'Line' if mark == 'line' else 'Bar'} chart of {lower_label(y['label'])} by {lower_label(x['label'])}, "
                   f"{len(rows)} points."}


def _views(sql: str) -> list[str]:
    return sorted(set(re.findall(r"\b((?:sem|lossless)\.[A-Za-z_][A-Za-z0-9_]*)", sql or "")))


_PHI_ASK = re.compile(r"\b(patient|member|ssn|social security|date of birth|dob|home address|identif\w*)\b", re.I)
_ABSTAIN_SUGGESTIONS = {
    "phi_request": ["How many denied claims are there by payer?", "Paid amount by month", "Denial rate by payer"],
    "data_unavailable": ["Denial rate by payer", "Paid amount trend by month", "Top denial reason codes (CARC)"],
    "low_confidence": ["Paid amount by payer", "How many claims are there?", "Denial rate by payer"],
    "out_of_scope": ["Denial rate by payer", "Paid amount trend by month", "How many claims are there?"],
}
_CATEGORIES = {"out_of_scope", "phi_request", "data_unavailable", "ambiguous", "low_confidence", "unsupported", "policy"}


def abstain_part(category: str, reason: str, details: str | None = None) -> dict:
    category = category if category in _CATEGORIES else "out_of_scope"
    part = {"type": "abstain", "id": new_id("p_"), "category": category, "reason": reason.strip() or
            "I can't answer that reliably with this data.",
            "suggestions": [{"id": new_id("g_"), "text": t} for t in _ABSTAIN_SUGGESTIONS.get(category, [])[:3]]}
    if details:
        part["details"] = _scrub(details)
    return part


def outcome_parts(answer: AnalyzerAnswer, principal: Principal, downloads: DownloadRegistry, *,
                  elapsed_ms: int | None = None, question: str = "", ask_args: dict | None = None,
                  refuse_args: dict | None = None, legacy_tools: bool = False) -> tuple[list[dict], str]:
    """Contract v1: parts + finishReason. Tool steps travel as live ``step.*`` events, so ``tool_call``
    parts are only included when ``legacy_tools`` is set (pre-v1 UI)."""
    prefix = tool_parts(answer.trace or []) if legacy_tools else []
    if answer.kind == "refusal":
        category = str((refuse_args or {}).get("category") or "")
        if category not in _CATEGORIES:
            category = "phi_request" if _PHI_ASK.search(question) or _PHI_ASK.search(answer.text or "") else "out_of_scope"
        return prefix + [abstain_part(category, answer.text or "")], "abstain"
    if answer.kind == "abstain":
        return prefix + [abstain_part("low_confidence", "I'm not confident enough to answer: the approaches I tried "
                                      "**disagreed**. Try narrowing the question (a payer, a date range or a metric "
                                      "definition).", details=answer.text)], "abstain"
    if answer.kind == "clarification":
        args = ask_args or {}
        choices = [{"id": str(c.get("id") or f"c{i}")[:64], "label": str(c.get("label") or "")[:200],
                    **({"description": str(c["description"])} if c.get("description") else {})}
                   for i, c in enumerate(args.get("choices") or []) if isinstance(c, dict) and c.get("label")]
        part = {"type": "clarification", "id": new_id("p_"), "clarificationId": new_id("q_"),
                "question": answer.text or str(args.get("question") or ""), "allowFreeText": True}
        if choices:
            part["choices"] = choices[:8]
        return prefix + [part], "needs_input"
    if answer.kind != "answer":
        return prefix + answer_parts(answer, principal, downloads, elapsed_ms, include_tools=False), "error"
    return prefix + answer_parts(answer, principal, downloads, elapsed_ms, include_tools=False), "stop"


def answer_parts(answer: AnalyzerAnswer, principal: Principal, downloads: DownloadRegistry,
                 elapsed_ms: int | None = None, include_tools: bool = True) -> list[dict]:
    parts = tool_parts(answer.trace or []) if include_tools else []
    if answer.kind != "answer":
        variant = {"refusal": "info", "clarification": "info"}.get(answer.kind)
        if variant:
            parts.append({"type": "text", "id": new_id("p_"), "text": answer.text})
        else:
            parts.append({"type": "error", "id": new_id("p_"), "code": "query_failed",
                          "title": "I couldn't answer that",
                          "message": "The analysis did not produce a verified result. Try rephrasing the question.",
                          "details": _scrub(answer.text), "retryable": True})
        return parts
    text = (answer.text or "").split("\nAssumptions: ")[0].strip() or "Here is the result."
    parts.append({"type": "text", "id": new_id("p_"), "text": text})
    sql_id = None
    if answer.sql:
        sql_id = new_id("p_")
        parts.append({"type": "code", "id": sql_id, "language": "sql", "code": answer.sql.strip(),
                      "title": "SQL", "role": "query", "collapsed": True, "rerunnable": False})
    names = list(answer.columns or [])
    rows = [list(r) for r in (answer.rows or [])]
    phi_cols = [n for n in names if is_phi_column(n)]
    if names:
        cols = columns_for(names, rows)
        sensitivity = "limited" if phi_cols else "deidentified"
        dl = downloads.put(principal, "result.csv", names, [[_jsonable(v) for v in r] for r in rows])
        table = {"type": "table", "id": new_id("p_"), "title": "Result", "columns": cols,
                 "rows": [[_jsonable(v) for v in r] for r in rows[:PREVIEW_ROWS]], "rowCount": len(rows),
                 "csvFileId": dl.id, "resultId": dl.id, "sensitivity": sensitivity,
                 "source": {"views": _views(answer.sql or ""), **({"elapsedMs": elapsed_ms} if elapsed_ms else {})}}
        if sql_id:
            table["sqlPartId"] = sql_id
        parts.append(table)
        if len(rows) > PREVIEW_ROWS:
            parts.append({"type": "file", "id": new_id("p_"), "fileId": dl.id, "name": "result.csv",
                          "mime": "text/csv", "sizeBytes": len(dl.csv_bytes()), "rowCount": len(rows),
                          "previewable": True, "sensitivity": sensitivity})
        chart = chart_for(cols, rows)
        if chart:
            parts.append(chart)
    if answer.assumptions:
        parts.append({"type": "callout", "id": new_id("p_"), "variant": "assumption", "title": "Assumptions",
                      "items": [{"id": new_id("a_"), "text": str(a), "editable": True} for a in answer.assumptions]})
    caveats = [w for w in (answer.warnings or []) if "redacted" not in str(w)]
    if caveats:
        parts.append({"type": "callout", "id": new_id("p_"), "variant": "caveat", "title": "Check before using",
                      "items": [{"id": new_id("a_"), "text": str(w)} for w in caveats]})
    if phi_cols:
        parts.append({"type": "callout", "id": new_id("p_"), "variant": "phi_redaction", "title": "Identifiers hidden",
                      "items": [{"id": new_id("a_"), "text": f"`{c}` is redacted in this session."} for c in phi_cols]})
    return parts


def _scrub(text: str) -> str:
    """Technical detail without literals: quoted strings and long digit runs are masked."""
    text = re.sub(r"'[^']*'", "'…'", text or "")
    text = re.sub(r"\d{4,}", "…", text)
    return text[:300]
