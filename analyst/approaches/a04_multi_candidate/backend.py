"""Completion backends. This package never constructs an API client.

A backend is anything with `complete(system, user, purpose) -> str`. `purpose` is
`generate:direct`, `generate:query_plan`, `generate:divide_and_conquer`, or `select`,
so a caller can route the selector to a stronger model without the protocol knowing names.

`ScriptedBackend` is the test double. A missing purpose raises KeyError: an agreement
case that accidentally calls the selector fails the test instead of inventing a vote.
"""
from __future__ import annotations

from collections.abc import Callable


class ScriptedBackend:
    def __init__(self, responses: dict[str, str | Callable[..., str]]):
        self.responses = responses
        self.calls: list[dict] = []

    def complete(self, *, system: str, user: str, purpose: str) -> str:
        if not isinstance(system, str) or not isinstance(user, str):
            raise TypeError("system and user must be strings")
        self.calls.append({"system": system, "user": user, "purpose": purpose})
        if purpose not in self.responses:
            raise KeyError(f"no scripted response for {purpose}")
        response = self.responses[purpose]
        text = response(system=system, user=user, purpose=purpose) if callable(response) else response
        if not isinstance(text, str):
            raise TypeError(f"scripted response for {purpose} returned {type(text).__name__}, expected str")
        return text
