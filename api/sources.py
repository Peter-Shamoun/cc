"""Citations: the data sources an answer used (contract 1.8.0, CONTRACT.md §14).

Built from what the run already emitted, with no extra queries: views named in SQL steps and in the answer's SQL/table,
and views the analyzer explored. Names are schema identifiers, never row values, so a source list carries no PHI.
"""
from __future__ import annotations

import re

_VIEW = re.compile(r"\b((?:sem|lossless)\.[A-Za-z_][A-Za-z0-9_]*)")

#: demo labels/descriptions for the semantic views (prod: the semantic layer's own catalog).
VIEW_INFO: dict[str, tuple[str, str]] = {
    "sem.claims": ("Claims", "One row per claim submission with billed, allowed and paid amounts, payer and status."),
    "sem.claim_lines": ("Claim lines", "Service lines on each claim: procedure codes, units and line amounts."),
    "sem.payers": ("Payers", "Payer directory: names, plan types and groupings."),
    "sem.payments": ("Payments", "Payments and adjustments posted from remittances (835)."),
    "sem.remit_claims": ("Remittance claims", "Claim-level remittance detail, including CARC/RARC codes."),
    "sem.provider_adjustments": ("Provider adjustments", "Provider-level adjustments from remittances (PLB)."),
}


def views_in(text: str) -> list[str]:
    return list(dict.fromkeys(_VIEW.findall(text or "")))


def label_for(view: str) -> str:
    if view in VIEW_INFO:
        return VIEW_INFO[view][0]
    words = view.split(".", 1)[-1].replace("_", " ").strip()
    return words[:1].upper() + words[1:]


def sources_for(parts: list[dict], steps: list[dict]) -> list[dict]:
    """``Source[]`` for one answer: views the answer used (``role: "answer"``) first, then ones only explored."""
    found: dict[str, dict] = {}

    def add(view: str, role: str, step_id: str | None = None, part_id: str | None = None):
        src = found.setdefault(view, {"name": view, "role": role, "stepIds": [], "partIds": []})
        if role == "answer":
            src["role"] = "answer"
        if step_id and step_id not in src["stepIds"]:
            src["stepIds"].append(step_id)
        if part_id and part_id not in src["partIds"]:
            src["partIds"].append(part_id)

    for step in steps:
        if step.get("type") == "step.sql_running":
            for view in views_in(step.get("sql", "")):
                add(view, "explored", step_id=step.get("stepId"))
        elif step.get("type") == "step.explore" and _VIEW.fullmatch(str(step.get("target") or "")):
            add(step["target"], "explored", step_id=step.get("stepId"))
    table_views: dict[str, list[str]] = {}
    for part in parts:
        if part.get("type") == "code" and part.get("role") == "query":
            for view in views_in(part.get("code", "")):
                add(view, "answer", part_id=part.get("id"))
        elif part.get("type") == "table":
            views = list((part.get("source") or {}).get("views") or [])
            table_views[part["id"]] = views
            for view in views:
                add(view, "answer", part_id=part["id"])
        elif part.get("type") == "artifact" and part.get("kind") in ("chart", "report") and table_views:
            for view in next(reversed(table_views.values())):  # demo artifacts are built from the latest table
                add(view, "answer", part_id=part.get("id"))
        elif part.get("type") == "chart" and table_views:
            for view in next(reversed(table_views.values())):  # charts plot the latest table
                add(view, "answer", part_id=part.get("id"))
    ordered = sorted(found.values(), key=lambda s: s["role"] != "answer")
    out = []
    for i, src in enumerate(ordered, 1):
        item = {"id": f"src_{i}", "kind": "view", "name": src["name"], "label": label_for(src["name"]),
                "role": src["role"]}
        if src["name"] in VIEW_INFO:
            item["description"] = VIEW_INFO[src["name"]][1]
        if src["stepIds"]:
            item["stepIds"] = src["stepIds"]
        if src["partIds"]:
            item["partIds"] = src["partIds"]
        out.append(item)
    return out
