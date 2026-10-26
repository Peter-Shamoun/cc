"""Conversation export (contract 1.8.0, CONTRACT.md §15): Markdown for people, JSON for tools.

The export is the user's own transcript, the same content GET /api/conversations/{id} returns. Markdown keeps table
previews (at most ``MAX_ROWS`` rows); the full result stays behind the CSV download. Filenames never carry the title
(a user rename could hold anything): ``cc_conversation_<yyyy-mm-dd>.<ext>``.
"""
from __future__ import annotations

import datetime
import time

MAX_ROWS = 50
FORMATS = {"markdown": ("text/markdown; charset=utf-8", "md"), "json": ("application/json", "json")}


def filename(fmt: str, when: float | None = None) -> str:
    day = datetime.datetime.fromtimestamp(time.time() if when is None else when, datetime.timezone.utc).date()
    return f"cc_conversation_{day.isoformat()}.{FORMATS[fmt][1]}"


def as_json(conv: dict, contract_version: str, exported_at: str) -> dict:
    return {"format": "cc.conversation", "contractVersion": contract_version, "exportedAt": exported_at,
            "conversation": conv}


def _cell(value) -> str:
    if isinstance(value, float):
        value = f"{value:.2f}" if abs(value) >= 1 else f"{value:.4g}"
    text = "" if value is None else str(value)
    return text.replace("\\", "\\\\").replace("|", "\\|").replace("\r", " ").replace("\n", " ")


def _table(part: dict) -> list[str]:
    cols = [c.get("label") or c.get("name") or "" for c in part.get("columns") or []]
    if not cols:
        return []
    rows = (part.get("rows") or [])[:MAX_ROWS]
    out = []
    if part.get("title"):
        out.append(f"**{part['title']}**")
        out.append("")
    out.append("| " + " | ".join(_cell(c) for c in cols) + " |")
    out.append("|" + "|".join(" --- " for _ in cols) + "|")
    out += ["| " + " | ".join(_cell(v) for v in row) + " |" for row in rows]
    total = int(part.get("rowCount") or len(rows))
    if total > len(rows):
        out.append("")
        out.append(f"_Showing {len(rows)} of {total} rows. Download the CSV in the app for the full result._")
    return out


def _part(part: dict) -> list[str]:
    kind = part.get("type")
    if kind == "text":
        return [part.get("text", "")]
    if kind == "code":
        title = [f"**{part['title']}**", ""] if part.get("title") else []
        return title + [f"```{part.get('language', '')}", part.get("code", "").rstrip(), "```"]
    if kind == "table":
        return _table(part)
    if kind == "chart":
        return [f"_Chart: {part.get('alt') or part.get('title') or 'chart'}_"]
    if kind == "callout":
        head = [f"**{part['title']}**"] if part.get("title") else []
        return head + [f"- {i.get('text', '')}" for i in part.get("items") or []]
    if kind == "abstain":
        return [f"> {part.get('reason', '')}"] + [f"> - Try: {s.get('text', '')}" for s in part.get("suggestions") or []]
    if kind == "clarification":
        return [f"> {part.get('question', '')}"] + [f"> - {c.get('label', '')}" for c in part.get("choices") or []]
    if kind == "error":
        return [f"> **{part.get('title', 'Error')}:** {part.get('message', '')}"]
    if kind == "file":
        return [f"_File: {part.get('name', 'download')} (download it in the app)_"]
    return []  # reasoning, status, tool_call, image, artifact: not part of a readable transcript


def as_markdown(conv: dict, exported_at: str) -> str:
    lines = [f"# {conv.get('title') or 'Conversation'}", "", f"_Exported from CC on {exported_at}._", ""]
    for msg in conv.get("messages") or []:
        if msg.get("role") == "user":
            lines += ["## Question", "", msg.get("text", ""), ""]
            continue
        lines += ["## Answer", ""]
        for part in msg.get("parts") or []:
            block = _part(part)
            if block:
                lines += block + [""]
        sources = msg.get("sources") or []
        if sources:
            lines += ["**Sources**", ""] + [f"- {s.get('label', s.get('name'))} (`{s.get('name')}`)"
                                             for s in sources if s.get("role", "answer") == "answer"] + [""]
        fb = msg.get("feedback")
        if fb:
            lines += [f"_Your feedback: {'👍' if fb.get('rating') == 'up' else '👎'}_", ""]
    return "\n".join(lines).rstrip() + "\n"
