"""Generate three SQL candidates, execute each one, then select.

The three generations always run, in strategy order, even if an earlier one refuses.
They are independent (same system prompt, no shared draft), so a production backend can
issue them concurrently. This implementation runs them in order so a scripted backend
is deterministic. The selector runs only when `select_candidates` asks for it, which is
zero times when the executed results agree and one time when they do not.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .prompts import (
    PURPOSE_SELECT,
    STRATEGIES,
    generation_purpose,
    generation_user,
    selector_system,
    selector_user,
    shared_system,
)
from .selector import Candidate, Selection, sanity_warnings, select_candidates

AGREEMENT_MODEL_CALLS = 3
SELECTOR_MODEL_CALLS = 4


@dataclass
class Decision:
    kind: str
    reason: str
    text: str
    sql: str | None = None
    rows: list | None = None
    columns: tuple = ()
    refused: bool = False
    abstained: bool = False
    selected_strategy: str | None = None
    selector_used: bool = False
    selector_reason: str = ""
    dropped_strategies: tuple = ()
    usage: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)
    candidates: list = field(default_factory=list)

    def record(self) -> dict:
        """JSON-ready summary. Candidate SQL is kept so a later scorer can compute the upper bound
        (the fraction of questions where at least one of the three was right) without this module
        seeing the gold answer."""
        return {
            "kind": self.kind,
            "reason": self.reason,
            "text": self.text,
            "sql": self.sql,
            "selected_strategy": self.selected_strategy,
            "selector_used": self.selector_used,
            "selector_reason": self.selector_reason,
            "dropped_strategies": list(self.dropped_strategies),
            "usage": self.usage,
            "candidates": [_candidate_record(c) for c in self.candidates],
        }


def run_protocol(question: str, schema_text: str, backend, executor, escalate: bool = False) -> Decision:
    if not question or not str(question).strip():
        raise ValueError("question is empty")
    system = shared_system(schema_text, escalate)
    candidates = [_generate(strategy, question, system, backend, executor) for strategy in STRATEGIES]
    selection = select_candidates(candidates, _chooser(question, backend))
    selector_calls = 1 if selection.selector_used else 0
    usage = {"generation_calls": len(STRATEGIES), "selector_calls": selector_calls,
             "model_calls": len(STRATEGIES) + selector_calls}
    text = _text(selection)
    return Decision(
        kind=selection.kind, reason=selection.reason, text=text, sql=selection.sql,
        rows=None if selection.rows is None else list(selection.rows), columns=selection.columns,
        refused=selection.kind == "refuse", abstained=selection.kind == "abstain",
        selected_strategy=selection.strategy, selector_used=selection.selector_used,
        selector_reason=selection.selector_reason, dropped_strategies=selection.dropped_strategies,
        usage=usage, trace=_trace(selection), candidates=candidates,
    )


def parse_generation(raw: str) -> tuple[str | None, str, str | None]:
    """Return `(sql, notes, refusal)`. A non-empty `refuse` string wins over `sql`."""
    obj = _json_object(raw)
    refusal = obj.get("refuse")
    if isinstance(refusal, str) and refusal.strip():
        return None, "", refusal.strip()
    sql = obj.get("sql")
    notes = obj.get("notes", "")
    if not isinstance(sql, str) or not sql.strip():
        raise ValueError("generation JSON has no sql")
    if not isinstance(notes, str):
        raise ValueError("generation notes must be a string")
    return sql.strip(), notes.strip(), None


def parse_choice(raw: str) -> tuple[str, str]:
    """Return `('a'|'b'|'abstain', reason)`. An unusable choice becomes abstain."""
    obj = _json_object(raw)
    choice = str(obj.get("choice", "")).strip().lower()
    reason = obj.get("reason", "")
    if not isinstance(reason, str):
        reason = str(reason)
    if choice not in ("a", "b", "abstain"):
        return "abstain", reason or "selector returned an unusable choice"
    return choice, reason.strip()


def _generate(strategy: str, question: str, system: str, backend, executor) -> Candidate:
    raw = _complete(backend, system=system, user=generation_user(strategy, question),
                    purpose=generation_purpose(strategy))
    candidate = Candidate(strategy=strategy, raw=raw)
    try:
        sql, notes, refusal = parse_generation(raw)
    except ValueError as e:
        candidate.error = f"unparseable generation: {e}"
        return candidate
    if refusal:
        candidate.refused = True
        candidate.refusal_reason = refusal
        return candidate
    candidate.sql = sql
    candidate.notes = notes
    execution = executor.execute(sql)
    if execution.error:
        candidate.error = execution.error
        return candidate
    candidate.columns = tuple(execution.columns)
    candidate.rows = tuple(tuple(row) for row in execution.rows)
    candidate.warnings = sanity_warnings(sql, candidate.columns, candidate.rows)
    return candidate


def _chooser(question: str, backend):
    def choose_pair(left: Candidate, right: Candidate) -> tuple[str, str]:
        raw = _complete(backend, system=selector_system(), user=selector_user(question, left, right),
                        purpose=PURPOSE_SELECT)
        try:
            return parse_choice(raw)
        except ValueError as e:
            return "abstain", f"unparseable selector reply: {e}"
    return choose_pair


def _complete(backend, *, system: str, user: str, purpose: str) -> str:
    text = backend.complete(system=system, user=user, purpose=purpose)
    if not isinstance(text, str):
        raise TypeError(f"backend returned {type(text).__name__} for {purpose}, expected str")
    return text


def _json_object(raw: str) -> dict:
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        text = text.rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object in model reply")
    try:
        obj = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"model reply is not JSON: {e}") from None
    if not isinstance(obj, dict):
        raise ValueError("model reply JSON is not an object")
    return obj


def _text(selection: Selection) -> str:
    if selection.kind == "refuse":
        return selection.reason.removeprefix("refuse: ").strip()
    if selection.kind == "abstain":
        return selection.reason.removeprefix("abstain: ").strip()
    dropped = f" Dropped from the comparison: {', '.join(selection.dropped_strategies)}." if selection.dropped_strategies else ""
    return f"{selection.reason}. Answer SQL is the {selection.strategy} candidate.{dropped}"


def _trace(selection: Selection) -> list:
    rows = [_candidate_record(c) for c in selection.candidates]
    rows.append({"event": "selection", "kind": selection.kind, "reason": selection.reason,
                 "strategy": selection.strategy, "selector_used": selection.selector_used,
                 "dropped_strategies": list(selection.dropped_strategies)})
    return rows


def _candidate_record(candidate: Candidate) -> dict:
    preview = [list(row) for row in candidate.rows[:20]]
    return {"strategy": candidate.strategy, "sql": candidate.sql, "notes": candidate.notes,
            "refused": candidate.refused, "refusal_reason": candidate.refusal_reason,
            "error": candidate.error, "warnings": list(candidate.warnings),
            "columns": list(candidate.columns), "row_count": len(candidate.rows),
            "rows_preview": preview}
