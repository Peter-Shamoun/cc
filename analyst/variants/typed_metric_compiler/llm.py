"""Pluggable LLM. Tests and the dry run use ScriptedLLM. No network client is constructed here."""
from __future__ import annotations

import json
from typing import Protocol


class LLM(Protocol):
    def complete(self, *, question: str, phase: str, turn: int, tools: list[dict],
                 error: str | None = None) -> dict:
        """Return {"tool": name, "input": {...}}."""


class ScriptedLLM:
    """Replay a list of tool calls for an exact question string. Used by tests and the hand dry run."""

    def __init__(self, scripts: dict[str, list[dict]]):
        self.scripts = {question: list(steps) for question, steps in scripts.items()}
        self.seen: list[dict] = []

    @classmethod
    def from_json(cls, path: str) -> "ScriptedLLM":
        return cls(json.loads(open(path).read()))

    def complete(self, *, question: str, phase: str, turn: int, tools: list[dict],
                 error: str | None = None) -> dict:
        steps = self.scripts.get(question)
        if not steps:
            raise KeyError(f"no scripted calls left for question: {question[:120]}")
        call = steps.pop(0)
        self.seen.append({"question": question, "phase": phase, "turn": turn, "error": error, "call": call})
        allowed = {t["name"] for t in tools}
        if call["tool"] not in allowed:
            raise RuntimeError(f"scripted tool {call['tool']} is not available in phase {phase}; have {sorted(allowed)}")
        return call
