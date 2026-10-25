"""Assumptions-first turn protocol.

The model may not run SQL until it has listed every reading of every open axis
and locked the standard one. In `state` mode (the benchmark) it then answers and
repeats the assumption sentences. In `material` mode it asks, and does not query,
when a fork on the ask list is still open. Refusal is legal from the first turn.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from .checklist import analyze, asking_axes, assumption_sentences, sql_violations


class ProtocolError(Exception):
    """The turn broke the protocol. The loop hands the message back and does not score it."""


@dataclass
class AgentResult:
    kind: str
    text: str
    sql: str | None = None
    columns: list | None = None
    rows: list | None = None
    assumptions: tuple[str, ...] = ()
    interpretations: tuple[dict, ...] = ()
    turns: int = 0
    sql_calls: int = 0


@dataclass
class Step:
    result: AgentResult | None = None
    tool_result: str = ""


@dataclass
class Protocol:
    question: str
    ask_mode: str
    decisions: list = field(init=False)
    phase: str = field(init=False, default="interpret")
    accepted: bool = field(init=False, default=False)
    sql_calls: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.ask_mode not in ("state", "material"):
            raise ValueError(f"ask_mode must be 'state' or 'material', not {self.ask_mode!r}")
        self.decisions = analyze(self.question)

    def apply(self, turn: dict, gateway) -> Step:
        kind = turn.get("kind")
        if kind == "refuse":
            reason = str(turn.get("text", "")).strip()
            if not reason:
                raise ProtocolError("refuse needs a reason")
            return Step(result=self._result("refusal", reason))
        if kind == "interpret":
            return self._interpret(turn)
        if kind == "sql":
            return self._sql(turn, gateway)
        if kind == "ask":
            return self._ask(turn)
        if kind == "answer":
            return self._answer(turn, gateway)
        raise ProtocolError(f"unknown turn kind {kind!r}; expected interpret, sql, answer, ask, or refuse")

    def _interpret(self, turn: dict) -> Step:
        if self.phase != "interpret":
            raise ProtocolError("the interpretation is already locked")
        got = self._axes(turn)
        expected = {decision.axis_id: decision for decision in self.decisions}
        if set(got) != set(expected):
            raise ProtocolError(
                "list exactly these axes before any SQL: "
                + (", ".join(expected) if expected else "(none)")
                + f"; got {sorted(got) or '(none)'}")
        for decision in self.decisions:
            item = got[decision.axis_id]
            considered = item.get("considered")
            if not isinstance(considered, list):
                raise ProtocolError(f"{decision.axis_id}: considered must be a list of reading ids")
            required = {reading.id for reading in decision.readings}
            if set(considered) != required:
                missing = sorted(required - set(considered))
                extra = sorted(set(considered) - required)
                raise ProtocolError(
                    f"{decision.axis_id}: list every reading {sorted(required)}"
                    + (f"; missing {missing}" if missing else "")
                    + (f"; unknown {extra}" if extra else ""))
            if item.get("chosen") != decision.chosen_id:
                raise ProtocolError(
                    f"{decision.axis_id}: lock {decision.chosen_id}. {decision.statement}")
        self.accepted = True
        if self.ask_mode == "material" and asking_axes(self.decisions):
            self.phase = "ask"
            prompts = " ".join(decision.ask_prompt for decision in asking_axes(self.decisions))
            ids = ", ".join(decision.axis_id for decision in asking_axes(self.decisions))
            return Step(tool_result=(
                "Interpretation recorded. These forks would change the number and the question does not pin them. "
                f"Ask the user and do not run SQL. Name these axis ids: {ids}. Suggested question: {prompts}"))
        self.phase = "query"
        sentences = assumption_sentences(self.decisions)
        copied = " ".join(sentences) if sentences else "The question pinned every axis. No assumption sentence is required."
        return Step(tool_result="Interpretation locked. You may query. Copy these sentences into the answer: " + copied)

    def _sql(self, turn: dict, gateway) -> Step:
        self._require_query_phase()
        sql = turn.get("sql") or ""
        self._guard_then_lock(sql, gateway)
        digest = gateway.execute(sql)
        self.sql_calls += 1
        preview = {**digest, "rows": digest["rows"][:20], "truncated": digest["row_count"] > 20}
        return Step(tool_result=json.dumps(preview, default=str))

    def _ask(self, turn: dict) -> Step:
        if self.phase != "ask":
            raise ProtocolError(
                "not asking: the readings are pinned or have a standard; state the assumption and answer")
        text = str(turn.get("text", "")).strip()
        if not text:
            raise ProtocolError("ask needs the clarifying question")
        missing = [decision.axis_id for decision in asking_axes(self.decisions) if decision.axis_id not in text]
        if missing:
            raise ProtocolError(f"the clarifying question must name these axes: {missing}")
        return Step(result=self._result("clarification", text))

    def _answer(self, turn: dict, gateway) -> Step:
        self._require_query_phase()
        sql = turn.get("sql") or ""
        text = str(turn.get("text", "")).strip()
        if not sql or not text:
            raise ProtocolError("answer needs sql and text")
        missing = [sentence for sentence in assumption_sentences(self.decisions) if sentence not in text]
        if missing:
            raise ProtocolError("the answer must include these assumption sentences unchanged: " + " | ".join(missing))
        self._guard_then_lock(sql, gateway)
        digest = gateway.execute(sql)
        self.sql_calls += 1
        return Step(result=self._result(
            "answer", text, sql=sql, columns=digest["columns"], rows=digest["rows"]))

    def _require_query_phase(self) -> None:
        if self.phase == "interpret":
            raise ProtocolError("submit interpret before any SQL, listing every reading and the locked one")
        if self.phase == "ask":
            raise ProtocolError("Ask the user before any SQL. The open fork would change the number.")

    def _guard_then_lock(self, sql: str, gateway) -> None:
        """Reject a statement the secure session must not run before checking the locked reading.

        The guard is the same check `QueryGateway.execute` repeats. Doing it here means a file
        read or a write is reported as a security error even when the SQL also misses the lock,
        and it still does not execute.
        """
        from bench.guard import GuardError, guard

        try:
            guard(sql, gateway.row_limit, gateway.escalate)
        except GuardError as error:
            raise ProtocolError(str(error)) from None
        self._require_lock_in_sql(sql)

    def _require_lock_in_sql(self, sql: str) -> None:
        violations = sql_violations(sql, self.decisions, self.question)
        if violations:
            raise ProtocolError("SQL does not implement the locked reading: " + "; ".join(violations))

    def _axes(self, turn: dict) -> dict:
        axes = turn.get("axes")
        if not isinstance(axes, list):
            raise ProtocolError("interpret needs an axes list")
        got = {}
        for item in axes:
            if not isinstance(item, dict) or "id" not in item:
                raise ProtocolError("each axis needs an id")
            if item["id"] in got:
                raise ProtocolError(f"duplicate axis {item['id']}")
            got[item["id"]] = item
        return got

    def _trace(self) -> tuple[dict, ...]:
        if not self.accepted:
            return ()
        return tuple({
            "axis": decision.axis_id,
            "chosen": decision.chosen_id,
            "pinned": decision.pinned,
            "ask": decision.ask_if_unpinned and not decision.pinned,
            "considered": [reading.id for reading in decision.readings],
            "assumptions": list(decision.assumptions),
        } for decision in self.decisions)

    def _result(self, kind: str, text: str, *, sql: str | None = None,
                columns: list | None = None, rows: list | None = None) -> AgentResult:
        return AgentResult(
            kind=kind, text=text, sql=sql, columns=columns, rows=rows,
            assumptions=tuple(assumption_sentences(self.decisions)) if self.accepted else (),
            interpretations=self._trace(), sql_calls=self.sql_calls)


def run(question: str, backend, gateway, *, ask_mode: str = "state", max_turns: int = 6) -> AgentResult:
    """Drive `backend(messages) -> turn` until it answers, asks, or refuses.

    `backend` is injected. This function does not call a model API. A scripted
    backend returns canned turns; a live one would call a model with the same
    messages. SQL goes through `gateway`, which is the secure session plus the guard.
    """
    protocol = Protocol(question, ask_mode)
    from .prompts import build_system_prompt

    messages: list[dict] = [
        {"role": "system", "content": build_system_prompt(ask_mode)},
        {"role": "user", "content": question},
    ]
    last_error: ProtocolError | None = None
    for turn_index in range(max_turns):
        raw = backend(messages)
        try:
            turn = json.loads(raw) if isinstance(raw, str) else raw
            if not isinstance(turn, dict):
                raise ProtocolError("turn must be a JSON object")
            messages.append({"role": "assistant", "content": json.dumps(turn)})
            step = protocol.apply(turn, gateway)
        except (ProtocolError, json.JSONDecodeError) as error:
            last_error = error if isinstance(error, ProtocolError) else ProtocolError(f"turn was not JSON: {error}")
            messages.append({"role": "user", "content": f"ERROR: {last_error}"})
            continue
        if step.result is not None:
            step.result.turns = turn_index + 1
            step.result.sql_calls = protocol.sql_calls
            return step.result
        messages.append({"role": "user", "content": step.tool_result})
    raise ProtocolError(f"ran out of turns. last error: {last_error}")
