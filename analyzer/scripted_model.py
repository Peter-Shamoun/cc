"""A Strands ``Model`` that plays a script instead of calling a provider: the offline demo's model and the tests'.

``ScriptedModel(next_turn)`` calls ``next_turn(messages, system_prompt)`` once per model request. It returns a
``ScriptedTurn``: optional text, then tool calls (``(command, arguments)``), each streamed as Strands events. No network,
no credentials. ``requests`` keeps what each request carried (system prompt blocks, messages, tool names) so a test can
check the prompt layout. ``seconds_per_turn`` paces the demo like a real model; the pause ends early on Stop.
"""
from __future__ import annotations

import asyncio
import copy
import itertools
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from strands.models.model import Model


@dataclass
class ScriptedTurn:
    text: str = ""
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)


class ScriptedModel(Model):
    def __init__(self, next_turn: Callable[[list, Any], ScriptedTurn], *, seconds_per_turn: float = 0.0,
                 usage_per_turn: dict | None = None):
        self.next_turn = next_turn
        self.seconds_per_turn = seconds_per_turn
        self.usage_per_turn = usage_per_turn or {"inputTokens": 1000, "outputTokens": 50, "totalTokens": 1050}
        self.requests: list[dict] = []
        self._tool_use_ids = itertools.count(1)

    def update_config(self, **model_config: Any) -> None:
        pass

    def get_config(self) -> dict:
        return {}

    def structured_output(self, output_model, prompt, system_prompt=None, **kwargs):
        raise NotImplementedError("the analyzer never asks for structured output")

    async def stream(self, messages, tool_specs=None, system_prompt=None, *, system_prompt_content=None,
                     cancel_signal=None, **kwargs):
        self.requests.append({"messages": copy.deepcopy(messages), "system_prompt_content": system_prompt_content,
                              "tool_names": [spec["name"] for spec in tool_specs or []]})
        waited = 0.0
        while waited < self.seconds_per_turn and not (cancel_signal is not None and cancel_signal.is_set()):
            await asyncio.sleep(min(0.05, self.seconds_per_turn - waited))
            waited += 0.05
        turn = self.next_turn(messages, system_prompt_content)
        yield {"messageStart": {"role": "assistant"}}
        if turn.text:
            yield {"contentBlockStart": {"start": {}}}
            yield {"contentBlockDelta": {"delta": {"text": turn.text}}}
            yield {"contentBlockStop": {}}
        for command, arguments in turn.tool_calls:
            tool_use_id = f"tooluse_{next(self._tool_use_ids)}"
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": tool_use_id, "name": command}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use" if turn.tool_calls else "end_turn"}}
        yield {"metadata": {"usage": dict(self.usage_per_turn),
                            "metrics": {"latencyMs": int(self.seconds_per_turn * 1000)}}}


def scripted_turns(*turns: ScriptedTurn) -> Callable[[list, Any], ScriptedTurn]:
    """A fixed list of turns; after the last one the model replies DONE."""
    remaining = list(turns)
    return lambda _messages, _system: remaining.pop(0) if remaining else ScriptedTurn(text="DONE")


def first_user_text(messages: list) -> str:
    for message in messages:
        if message.get("role") == "user":
            return "".join(block.get("text", "") for block in message.get("content", []) if "text" in block)
    return ""


def assistant_turns(messages: list) -> int:
    return sum(1 for message in messages if message.get("role") == "assistant")


def tool_results(messages: list) -> list[str]:
    """Every tool result's text so far, oldest first."""
    texts = []
    for message in messages:
        for block in message.get("content", []):
            if "toolResult" in block:
                texts.append("".join(item.get("text", "") for item in block["toolResult"].get("content", [])))
    return texts
