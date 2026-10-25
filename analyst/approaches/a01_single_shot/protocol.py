"""A01 protocol: one model completion, then at most one execution.

The completion must be exactly one tool call.
``submit_sql`` executes that statement once and the rows become the answer text.
``refuse`` executes nothing.
Any other shape (no call, two calls, an unknown tool, a missing statement) executes nothing
and is not sent back to the model. An execution error is also terminal: the statement is kept
and the model is not asked to repair it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from .backend import ModelRequest, ModelResponse, ToolCall
from .prompts import render_answer_text, render_system_prompt, render_user_prompt
from .tools import TOOL_SPEC

EFFORT = "low"


@dataclass
class Outcome:
    kind: str  # answer | refusal | protocol_error | execution_error
    sql: str | None
    text: str
    model_calls: int
    executions: int
    error: str | None = None
    usage: dict = field(default_factory=dict)
    tool_calls: list[dict] = field(default_factory=list)


def _arguments(call: ToolCall) -> dict | None:
    raw = call.arguments
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw, dict):
        return None
    return raw


def _record(call: ToolCall) -> dict:
    arguments = call.arguments
    if isinstance(arguments, str):
        return {"name": call.name, "arguments": arguments}
    return {"name": call.name, "arguments": arguments}


def run_single_shot(question: str, backend, executor) -> Outcome:
    """Ask ``backend`` once. Execute with ``executor`` only when the completion is a single ``submit_sql``."""
    system = render_system_prompt(executor.dictionary())
    user = render_user_prompt(question)
    response: ModelResponse = backend.complete(
        ModelRequest(system=system, user=user, tools=TOOL_SPEC, effort=EFFORT)
    )
    calls = list(response.tool_calls)
    recorded = [_record(call) for call in calls]
    if len(calls) != 1:
        return Outcome(
            kind="protocol_error", sql=None, text="", model_calls=1, executions=0,
            error=f"expected exactly one tool call, got {len(calls)}",
            usage=dict(response.usage), tool_calls=recorded,
        )
    call = calls[0]
    arguments = _arguments(call)
    if call.name == "refuse":
        reason = ""
        if arguments is not None:
            reason = str(arguments.get("reason") or "")
        return Outcome(
            kind="refusal", sql=None, text=reason or "refused", model_calls=1, executions=0,
            usage=dict(response.usage), tool_calls=recorded,
        )
    if call.name != "submit_sql":
        return Outcome(
            kind="protocol_error", sql=None, text="", model_calls=1, executions=0,
            error=f"unknown tool {call.name!r}; expected submit_sql or refuse",
            usage=dict(response.usage), tool_calls=recorded,
        )
    sql = arguments.get("sql") if arguments is not None else None
    if not isinstance(sql, str) or not sql.strip():
        return Outcome(
            kind="protocol_error", sql=None, text="", model_calls=1, executions=0,
            error="submit_sql did not include a sql string",
            usage=dict(response.usage), tool_calls=recorded,
        )
    try:
        result = executor.execute(sql)
    except Exception as exc:  # noqa: BLE001 — the failure is the answer; it is not a cue to call the model again
        message = f"{type(exc).__name__}: {exc}".splitlines()[0][:400]
        return Outcome(
            kind="execution_error", sql=sql, text=f"execution failed: {message}",
            model_calls=1, executions=1, error=message,
            usage=dict(response.usage), tool_calls=recorded,
        )
    return Outcome(
        kind="answer", sql=sql, text=render_answer_text(result.columns, result.rows),
        model_calls=1, executions=1, usage=dict(response.usage), tool_calls=recorded,
    )
