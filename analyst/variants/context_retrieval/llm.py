"""Pluggable completion interface. The default backend is scripted and makes no network calls."""
from __future__ import annotations

from dataclasses import dataclass, field


class LLMNotScripted(RuntimeError):
    """The scripted backend has no answer for this question. It will not call a paid API."""


@dataclass
class LLMRequest:
    question_id: str | None
    question: str
    system: str
    user: str
    tools: list[dict]


@dataclass
class LLMResponse:
    sql: str | None = None
    text: str = ""
    refused: bool = False
    usage: dict = field(default_factory=dict)


class ScriptedLLM:
    """Maps a question id or the exact question text to a prepared response."""

    def __init__(self, scripts: dict[str, LLMResponse] | None = None):
        self.scripts = scripts or {}
        self.seen: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.seen.append(request)
        response = self.scripts.get(request.question_id or "") or self.scripts.get(request.question)
        if response is None:
            raise LLMNotScripted(
                "no scripted completion for this question; refusing to call an external model"
            )
        return response
