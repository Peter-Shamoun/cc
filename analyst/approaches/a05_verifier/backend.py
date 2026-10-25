"""Model backends. This package does not call a paid API.

A live screen passes its own backend into the adapter. Tests pass ScriptedBackend, which
returns canned completions and records the exact system and user messages so a test can
see what the critic was shown.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ModelCompletion:
    text: str


class LanguageModel(Protocol):
    def complete(self, *, role: str, system: str, user: str, phase: str) -> ModelCompletion:
        """One completion. role is 'analyst' or 'critic'; phase names the protocol step."""


@dataclass
class ScriptedBackend:
    """Completions in call order. The next call's role has to match the next scripted role."""

    responses: list[tuple[str, str]]
    calls: list[dict] = field(default_factory=list)

    def complete(self, *, role: str, system: str, user: str, phase: str) -> ModelCompletion:
        if not self.responses:
            raise RuntimeError(f"scripted backend has no completion left for role {role!r} phase {phase!r}")
        expected_role, text = self.responses.pop(0)
        if expected_role != role:
            raise RuntimeError(f"scripted backend expected role {expected_role!r}, got {role!r} during {phase}")
        self.calls.append({"role": role, "phase": phase, "system": system, "user": user})
        return ModelCompletion(text=text)
