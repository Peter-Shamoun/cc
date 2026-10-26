"""Multi-question clarification (contract 1.23.0, CONTRACT.md §6.1), modelled on Claude's ask-the-user form.

A ``clarification`` part may carry ``questions``: 1–4 questions, each with a ``header`` of 12 characters or fewer,
2–4 options (``label`` + ``description``) and an optional ``multiSelect``. Every question also accepts free text
("Other"), and any question can be skipped. The client answers with ``ChatRequest.resume.answers``.

``normalize_questions`` turns model arguments into contract questions (and drops what can't be shown).
``validate_answers`` is the server's 422 rule. ``summary`` is the canonical one-line text the analyzer receives after
``Clarification:``. All three are mirrored in ``web/src/contract/clarify.ts`` and share ``api/contract/clarify_cases.json``.
"""
from __future__ import annotations

import re
from typing import Any

MAX_QUESTIONS = 4
MIN_OPTIONS, MAX_OPTIONS = 2, 4
HEADER_MAX = 12
QUESTION_MAX = 500
LABEL_MAX = 60
DESCRIPTION_MAX = 200
OTHER_MAX = 500
SKIPPED = "no preference"
_ID = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
_SPACE = re.compile(r"\s+")


def _clean(value: Any, limit: int) -> str:
    """One line, trimmed, at most ``limit`` characters (code points)."""
    return _SPACE.sub(" ", str(value if value is not None else "")).strip()[:limit].rstrip()


def normalize_questions(raw: Any) -> list[dict]:
    """Model ``ask`` arguments → contract ``ClarifyQuestion``s. A question without a header, text or 2 valid options is
    dropped; extra options and questions past 4 are cut; ids are made unique. Empty list = not a multi-question ask."""
    out: list[dict] = []
    seen: set[str] = set()
    for i, q in enumerate(raw if isinstance(raw, list) else []):
        if not isinstance(q, dict) or len(out) == MAX_QUESTIONS:
            continue
        header, text = _clean(q.get("header"), HEADER_MAX), _clean(q.get("question"), QUESTION_MAX)
        qid = str(q.get("id") or f"q{i + 1}")
        if not header or not text or not _ID.fullmatch(qid) or qid in seen:
            continue
        options, opt_seen = [], set()
        for j, o in enumerate(q.get("options") or []):
            if not isinstance(o, dict) or len(options) == MAX_OPTIONS:
                continue
            oid, label = str(o.get("id") or f"o{j + 1}"), _clean(o.get("label"), LABEL_MAX)
            description = _clean(o.get("description"), DESCRIPTION_MAX)
            if label and description and _ID.fullmatch(oid) and oid not in opt_seen:
                opt_seen.add(oid)
                options.append({"id": oid, "label": label, "description": description})
        if len(options) < MIN_OPTIONS:
            continue
        seen.add(qid)
        out.append({"id": qid, "header": header, "question": text, "options": options,
                    **({"multiSelect": True} if q.get("multiSelect") is True else {})})
    return out


def validate_answers(questions: list[dict], answers: list[dict]) -> str | None:
    """None when ``answers`` is a valid reply to ``questions``, else a user-safe message (422)."""
    if len(answers) > MAX_QUESTIONS:
        return "Too many answers."
    by_id = {q["id"]: q for q in questions}
    seen: set[str] = set()
    for a in answers:
        q = by_id.get(a.get("questionId"))
        if q is None:
            return "An answer refers to a question that wasn't asked."
        if q["id"] in seen:
            return "A question was answered twice."
        seen.add(q["id"])
        choices = list(a.get("choiceIds") or [])
        other = _clean(a.get("otherText"), OTHER_MAX)
        if a.get("skipped"):
            if choices or other:
                return "A skipped question can't also have an answer."
            continue
        if not choices and not other:
            return "Pick an option, type an answer, or skip the question."
        valid = {o["id"] for o in q["options"]}
        if len(set(choices)) != len(choices) or not set(choices) <= valid:
            return "An answer picks an option that wasn't offered."
        if not q.get("multiSelect") and len(choices) + (1 if other else 0) > 1:
            return "That question takes one answer."
    return None


def summary(questions: list[dict], answers: list[dict]) -> str:
    """``Header: value; Header: value`` in question order. Labels in option order, then the free text; an unanswered
    or skipped question reads ``no preference``. Assumes ``validate_answers`` passed."""
    by_q = {a.get("questionId"): a for a in answers}
    out = []
    for q in questions:
        a = by_q.get(q["id"]) or {"skipped": True}
        picked = set(a.get("choiceIds") or [])
        values = [o["label"] for o in q["options"] if o["id"] in picked]
        other = _clean(a.get("otherText"), OTHER_MAX)
        if other:
            values.append(other)
        out.append(f"{q['header']}: {', '.join(values) if values and not a.get('skipped') else SKIPPED}")
    return "; ".join(out)


def legacy_answers(questions: list[dict], choice_ids: list[str], text: str | None) -> list[dict]:
    """A pre-1.23 resume (``choiceIds``/``text``) answers the first question (its options are mirrored in ``choices``);
    the rest count as skipped."""
    q = questions[0]
    valid = [c for c in choice_ids if c in {o["id"] for o in q["options"]}]
    if not q.get("multiSelect"):
        valid = valid[:1]
    other = _clean(text, OTHER_MAX) if not valid else ""
    if not valid and not other:
        return []
    return [{"questionId": q["id"], **({"choiceIds": valid} if valid else {}), **({"otherText": other} if other else {})}]
