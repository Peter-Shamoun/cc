"""Pluggable LLM interface. Tests and the dry run use ``ScriptedLLM``.

This variant does not call a paid API. A production runner wraps its own client
in the same ``complete`` method; nothing here opens a network connection.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ToolCall:
    name: str
    arguments: dict
    call_id: str = "call"


@dataclass
class LLMResponse:
    """One model turn. ``usage`` keys match the harness token counters."""

    text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_input_tokens": 0,
        "cache_creation_input_tokens": 0,
    })
    stop_reason: str = "end_turn"


class LLM:
    """Override ``complete``. ``strategy`` is the candidate id (or ``__selector__`` / ``__verifier__``)."""

    def complete(self, *, system: str, messages: list, tools: list, model: str, strategy: str) -> LLMResponse:
        raise NotImplementedError


class ScriptedLLM(LLM):
    """Returns canned turns keyed by strategy. Each call pops the next turn, so a candidate that
    explores and then answers needs two scripted responses in order."""

    def __init__(self, scripts: dict[str, list[LLMResponse]]):
        self.scripts = {key: list(turns) for key, turns in scripts.items()}
        self.calls: list[dict] = []

    def complete(self, *, system: str, messages: list, tools: list, model: str, strategy: str) -> LLMResponse:
        self.calls.append({
            "strategy": strategy,
            "model": model,
            "system_chars": len(system),
            "user_chars": sum(len(str(m.get("content", ""))) for m in messages),
        })
        queue = self.scripts.get(strategy)
        if not queue:
            raise RuntimeError(f"no scripted response left for strategy {strategy!r}")
        return queue.pop(0)


class UnconfiguredLLM(LLM):
    """Default backend. Fails on the first turn instead of opening a client."""

    def complete(self, *, system: str, messages: list, tools: list, model: str, strategy: str) -> LLMResponse:
        raise RuntimeError(
            "No LLM backend is configured. Pass a ScriptedLLM (tests, dry run) or an object with "
            "complete(system, messages, tools, model, strategy) -> LLMResponse. "
            "This variant does not call a paid API."
        )


def final_answer(sql: str, answer: str = "", usage: dict | None = None) -> LLMResponse:
    return LLMResponse(
        tool_calls=[ToolCall("final_answer", {"sql": sql, "answer": answer})],
        usage=usage or {},
    )


def run_sql(sql: str, usage: dict | None = None) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall("run_sql", {"sql": sql})], usage=usage or {})


def refuse(reason: str, usage: dict | None = None) -> LLMResponse:
    return LLMResponse(tool_calls=[ToolCall("refuse", {"reason": reason})], usage=usage or {})
