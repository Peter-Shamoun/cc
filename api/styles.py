"""Answer styles for regenerate (contract 1.10.0, CONTRACT.md §8.1).

`regenerate.style` re-runs an answer and replaces it in place as a new version (a sibling via `regenerateOf`).
Styles change only the prose. Data parts are kept as they are: tables, SQL, artifacts, clarifications, errors,
and every assumption/caveat callout, so a shorter answer never hides a caveat. Charts are the one exception.

* ``concise``  - the prose is cut to its first sentence, and a chart is dropped when a table holds the same data.
* ``detailed`` - a "How this was computed" paragraph is added, built only from part metadata (result shape,
                 the query part, the number of assumptions and caveats), never from row values.

This runs on the server for every backend, so demo, sim and prod behave the same. A model backend may also use
the style as a prompt hint; the contract only promises the shape above.
"""
from __future__ import annotations

import re

STYLES = ("default", "concise", "detailed")
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z*\"(])")


def first_sentence(text: str) -> str:
    para = text.strip().split("\n\n", 1)[0].strip()
    return _SENTENCE.split(para, 1)[0].strip() if para else text.strip()


def _how(parts: list[dict]) -> str:
    bits = []
    table = next((p for p in parts if p.get("type") == "table"), None)
    if table is not None:
        cols = ", ".join(c.get("label") or c.get("key", "") for c in table.get("columns", []))
        n = table.get("rowCount", len(table.get("rows", [])))
        bits.append(f"The result has {n} row{'s' if n != 1 else ''} with these columns: {cols}.")
    if any(p.get("type") == "code" and p.get("role", "query") == "query" for p in parts):
        bits.append("The exact SQL is in the query block, and it ran read-only against the semantic views listed under Sources.")
    callouts = [p for p in parts if p.get("type") == "callout"]
    assumptions = sum(len(p.get("items", [])) for p in callouts if p.get("variant") == "assumption")
    caveats = sum(len(p.get("items", [])) for p in callouts if p.get("variant") != "assumption")
    if assumptions or caveats:
        bits.append(f"It relies on {assumptions} assumption{'s' if assumptions != 1 else ''} and has "
                    f"{caveats} caveat{'s' if caveats != 1 else ''}, listed below. Edit an assumption to re-run it.")
    if any(p.get("type") == "chart" or (p.get("type") == "artifact" and p.get("kind") == "chart") for p in parts):
        bits.append("The chart plots the same rows as the table.")
    return "**How this was computed.** " + " ".join(bits) if bits else ""


def apply_style(parts: list[dict], style: str | None) -> list[dict]:
    """Return the parts restyled. ``default`` or ``None`` returns them unchanged."""
    if style not in ("concise", "detailed"):
        return parts
    texts = [p for p in parts if p.get("type") == "text"]
    if not texts:
        return parts
    out = []
    has_table = any(p.get("type") == "table" for p in parts)
    for p in parts:
        if style == "concise":
            if p.get("type") == "chart" and has_table:
                continue
            if p.get("type") == "text":
                p = {**p, "text": first_sentence(p["text"])}
        elif style == "detailed" and p is texts[-1]:
            how = _how(parts)
            if how:
                p = {**p, "text": p["text"].rstrip() + "\n\n" + how}
        out.append(p)
    return out
