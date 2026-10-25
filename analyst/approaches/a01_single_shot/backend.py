"""Model call shape for A01. One completion, then the protocol stops.

``ScriptedBackend`` replays responses queued by a test. It never opens a network connection.
A live backend is a separate object the caller passes in; this module does not construct one.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    name: str
    arguments: dict | str


@dataclass
class ModelRequest:
    system: str
    user: str
    tools: list[dict]
    effort: str


@dataclass
class ModelResponse:
    tool_calls: list[ToolCall] = field(default_factory=list)
    text: str = ""
    usage: dict = field(default_factory=dict)


def submit_sql_response(sql: str) -> ModelResponse:
    return ModelResponse(tool_calls=[ToolCall(name="submit_sql", arguments={"sql": sql})])


def refuse_response(reason: str) -> ModelResponse:
    return ModelResponse(tool_calls=[ToolCall(name="refuse", arguments={"reason": reason})])


class ScriptedBackend:
    """Return queued completions in order. A further call raises, which is how a test catches a repair."""

    def __init__(self, responses: list[ModelResponse]):
        self._responses = list(responses)
        self.requests: list[ModelRequest] = []

    def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if not self._responses:
            raise RuntimeError("scripted backend has no further response; the protocol made another model call")
        return self._responses.pop(0)
