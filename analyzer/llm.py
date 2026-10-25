"""Pluggable completion backends.

The loop talks to one interface: ``complete(...) -> Completion``. ``AnthropicLLM``
and ``BedrockLLM`` build the request a paid call would send, and refuse to send
it unless ``allow_paid_api`` is set. Tests use ``ScriptedLLM``, which replays a
fixed script and never touches the network. This package does not read API keys.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable


class PaidAPIDisabled(RuntimeError):
    """Raised instead of calling Anthropic or Bedrock."""


@dataclass
class ToolCall:
    name: str
    arguments: dict
    id: str


@dataclass
class Completion:
    text: str
    tool_calls: list[ToolCall]
    stop_reason: str
    usage: dict
    model: str


def anthropic_request_kwargs(*, model: str, system: str, messages: list, tools: list, max_tokens: int,
                             effort: str | None, prompt_cache: bool) -> dict:
    """The Messages API payload. No temperature: current models reject sampling params."""
    system_param: str | list = system
    if prompt_cache:
        system_param = [{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}]
    kwargs: dict[str, Any] = {
        "model": model,
        "max_tokens": max_tokens,
        "system": system_param,
        "tools": tools,
        "messages": messages,
    }
    if effort and effort != "default":
        kwargs["output_config"] = {"effort": effort}
    return kwargs


def bedrock_request_body(*, system: str, messages: list, tools: list, max_tokens: int, effort: str | None,
                         prompt_cache: bool) -> dict:
    """Anthropic-on-Bedrock body. The model id is the invoke_model modelId, not a body field."""
    body: dict[str, Any] = {
        "anthropic_version": "bedrock-2023-05-31",
        "max_tokens": max_tokens,
        "system": system if not prompt_cache else [
            {"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
        "messages": messages,
        "tools": tools,
    }
    if effort and effort != "default":
        body["output_config"] = {"effort": effort}
    return body


def completion_from_blocks(blocks: list, *, model: str, stop_reason: str, usage: dict | None) -> Completion:
    text: list[str] = []
    calls: list[ToolCall] = []
    for block in blocks:
        kind = block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
        if kind == "text":
            text.append(block.get("text") if isinstance(block, dict) else block.text)
        elif kind == "tool_use":
            if isinstance(block, dict):
                calls.append(ToolCall(name=block["name"], arguments=dict(block.get("input") or {}), id=block["id"]))
            else:
                calls.append(ToolCall(name=block.name, arguments=dict(block.input), id=block.id))
    return Completion(
        text="\n".join(text),
        tool_calls=calls,
        stop_reason=stop_reason or ("tool_use" if calls else "end_turn"),
        usage=dict(usage or {}),
        model=model,
    )


class AnthropicLLM:
    """Anthropic Messages API. ``complete`` raises ``PaidAPIDisabled`` unless explicitly allowed."""

    def __init__(self, allow_paid_api: bool = False):
        self.allow_paid_api = allow_paid_api

    def complete(self, *, model: str, system: str, messages: list, tools: list, max_tokens: int,
                 effort: str | None = None, prompt_cache: bool = False, candidate_index: int = 0) -> Completion:
        del candidate_index
        kwargs = anthropic_request_kwargs(model=model, system=system, messages=messages, tools=tools,
                                           max_tokens=max_tokens, effort=effort, prompt_cache=prompt_cache)
        if not self.allow_paid_api:
            raise PaidAPIDisabled(
                "Anthropic API calls are disabled. Use ScriptedLLM in tests, or construct "
                "AnthropicLLM(allow_paid_api=True) for an explicit paid run. This package does not add API keys.")
        import anthropic
        response = anthropic.Anthropic().messages.create(**kwargs)
        usage = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
        return completion_from_blocks(response.content, model=model, stop_reason=response.stop_reason, usage=usage)


class BedrockLLM:
    """Anthropic models via Bedrock Runtime. Same paid-call gate as ``AnthropicLLM``."""

    def __init__(self, allow_paid_api: bool = False, region_name: str = "us-west-2"):
        self.allow_paid_api = allow_paid_api
        self.region_name = region_name

    def complete(self, *, model: str, system: str, messages: list, tools: list, max_tokens: int,
                 effort: str | None = None, prompt_cache: bool = False, candidate_index: int = 0) -> Completion:
        del candidate_index
        body = bedrock_request_body(system=system, messages=messages, tools=tools, max_tokens=max_tokens,
                                    effort=effort, prompt_cache=prompt_cache)
        if not self.allow_paid_api:
            raise PaidAPIDisabled(
                "Bedrock calls are disabled. Use ScriptedLLM in tests, or construct "
                "BedrockLLM(allow_paid_api=True) for an explicit paid run under the BAA. "
                "This package does not add credentials.")
        import json
        import boto3
        client = boto3.client("bedrock-runtime", region_name=self.region_name)
        response = client.invoke_model(modelId=model, body=json.dumps(body),
                                       contentType="application/json", accept="application/json")
        payload = json.loads(response["body"].read())
        return completion_from_blocks(payload.get("content", []), model=model,
                                      stop_reason=payload.get("stop_reason", ""), usage=payload.get("usage"))


Turn = dict | Callable[..., dict]


class ScriptedLLM:
    """Deterministic replay. Never calls a network.

    ``scripts`` is a list of turns, used for every model, or a dict keyed by model
    name or by ``(model, candidate_index)``. Each candidate and model has its own
    cursor, so candidates do not consume each other's turns. A turn is
    ``{"text": str, "tool_calls": [{"name", "arguments"}]}`` or a callable
    ``(model, messages, candidate_index) -> turn``. After the script ends, the
    next completion has no tool call.
    """

    def __init__(self, scripts: list[Turn] | dict):
        self.scripts = scripts
        self.calls: list[dict] = []
        self._cursors: dict = defaultdict(int)
        self._ids = 0

    def complete(self, *, model: str, system: str, messages: list, tools: list, max_tokens: int,
                 effort: str | None = None, prompt_cache: bool = False, candidate_index: int = 0) -> Completion:
        del max_tokens, prompt_cache
        key, script = self._resolve(model, candidate_index)
        self.calls.append({
            "model": model,
            "effort": effort,
            "candidate_index": candidate_index,
            "n_messages": len(messages),
            "tools": [tool["name"] for tool in tools],
            "has_pitfalls": "Invariants and common traps" in system,
            "has_refusal": "patient names" in system.lower(),
            "has_dictionary": "Data dictionary" in system,
            "has_profile": "Data profile" in system,
        })
        cursor = self._cursors[key]
        self._cursors[key] += 1
        if cursor >= len(script):
            return Completion(text="script exhausted", tool_calls=[], stop_reason="end_turn",
                              usage={"input_tokens": 0, "output_tokens": 0}, model=model)
        turn = script[cursor]
        if callable(turn):
            turn = turn(model, messages, candidate_index)
        return self._completion(turn, model)

    def _resolve(self, model: str, candidate_index: int) -> tuple:
        if isinstance(self.scripts, list):
            return (model, candidate_index), self.scripts
        pair = (model, candidate_index)
        if pair in self.scripts:
            return pair, self.scripts[pair]
        if model in self.scripts:
            return pair, self.scripts[model]
        raise KeyError(f"no script for model {model!r} candidate {candidate_index}")

    def _completion(self, turn: dict, model: str) -> Completion:
        calls = []
        for spec in turn.get("tool_calls") or []:
            self._ids += 1
            calls.append(ToolCall(name=spec["name"], arguments=dict(spec.get("arguments") or {}),
                                  id=spec.get("id") or f"toolu_script_{self._ids}"))
        return Completion(text=turn.get("text") or "", tool_calls=calls,
                          stop_reason="tool_use" if calls else "end_turn",
                          usage={"input_tokens": 0, "output_tokens": 0}, model=model)
