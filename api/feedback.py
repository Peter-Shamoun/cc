"""Answer feedback: thumbs up/down with reasons (contract 1.7.0, CONTRACT.md §13).

Stored on the assistant message in the user's own transcript (``StoredMessage.feedback``). The free-text comment can
contain anything the user types, so it is treated like message text: never logged or audited. The audit event
carries only the rating and the reason ids.
"""
from __future__ import annotations

import time

#: (id, label, rating). Ids are stable API values; labels are demo copy the UI may show as chips.
REASONS: list[dict] = [
    {"id": "correct", "label": "Correct", "rating": "up"},
    {"id": "clear", "label": "Easy to understand", "rating": "up"},
    {"id": "fast", "label": "Fast", "rating": "up"},
    {"id": "wrong_numbers", "label": "Numbers look wrong", "rating": "down"},
    {"id": "wrong_query", "label": "Wrong filters or SQL", "rating": "down"},
    {"id": "misunderstood", "label": "Misunderstood the question", "rating": "down"},
    {"id": "missing_data", "label": "Missing data", "rating": "down"},
    {"id": "too_slow", "label": "Too slow", "rating": "down"},
    {"id": "other", "label": "Something else", "rating": "down"},
]
_RATING = {r["id"]: r["rating"] for r in REASONS}
MAX_COMMENT = 1000


def check(rating: str, reasons: list[str]) -> str | None:
    """None when every reason exists and belongs to ``rating``; otherwise a user-facing message."""
    for reason in reasons:
        if reason not in _RATING:
            return "Unknown feedback reason."
        if _RATING[reason] != rating:
            return "That reason doesn't match the rating."
    return None


def record(rating: str, reasons: list[str], comment: str | None) -> dict:
    out = {"rating": rating, "reasons": list(dict.fromkeys(reasons)),
           "updatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    if comment and comment.strip():
        out["comment"] = comment.strip()
    return out


def apply(conv: dict, message_id: str, feedback: dict | None) -> str:
    """Set (or clear, with None) feedback on ``message_id`` in ``conv``. Returns 'ok', 'missing' or 'not_assistant'."""
    for message in conv["messages"]:
        if message.get("id") == message_id:
            if message.get("role") != "assistant":
                return "not_assistant"
            if feedback is None:
                message.pop("feedback", None)
            else:
                message["feedback"] = feedback
            return "ok"
    return "missing"
