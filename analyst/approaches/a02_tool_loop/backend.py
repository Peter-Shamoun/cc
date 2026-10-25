"""Model backends for the A02 loop.

ScriptedBackend replays a list of tool calls. It is the only backend this package
runs. There is no Anthropic client here, so a test or a local drive cannot spend
API quota by falling through to a default model.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


def rough_token_count(value) -> int:
    """Characters divided by four. A planning figure, not a tokenizer count."""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, default=str)
    return max(1, (len(text) + 3) // 4)


@dataclass
class ToolCall:
    call_id: str
    name: str
    arguments: dict


@dataclass
class ModelTurn:
    text: str
    tool_calls: list[ToolCall]
    usage: dict = field(default_factory=dict)


class ScriptExhausted(RuntimeError):
    """The script ended before the protocol had submitted an answer or a refusal."""


class ScriptedBackend:
    """Return the next scripted tool call. Each step is {tool, arguments, text?}."""

    def __init__(self, script: list[dict]):
        self.script = list(script)
        self.turns_completed = 0
        self.seen_messages: list[list] = []

    def complete(self, messages: list, tools: list) -> ModelTurn:
        if self.turns_completed >= len(self.script):
            raise ScriptExhausted(
                f"scripted backend has {len(self.script)} steps; the protocol asked for step {self.turns_completed + 1}"
            )
        step = self.script[self.turns_completed]
        self.turns_completed += 1
        self.seen_messages.append(messages)
        arguments = step.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise TypeError("scripted arguments must be an object")
        text = step.get("text") or ""
        usage = {
            "input_tokens": rough_token_count(messages) + rough_token_count(tools),
            "output_tokens": rough_token_count(text) + rough_token_count(arguments) + 8,
        }
        return ModelTurn(
            text=text,
            tool_calls=[ToolCall(call_id=f"scripted_{self.turns_completed}", name=step["tool"], arguments=arguments)],
            usage=usage,
        )
