"""Errors the model is meant to read."""
from __future__ import annotations


class SpecError(ValueError):
    """The JSON is not a spec yet. Fix the shape. This does not open the SQL fallback."""


class CannotExpress(ValueError):
    """A well-formed request this compiler will not turn into SQL. This opens the SQL fallback."""

    def __init__(self, reason: str, hint: str = ""):
        self.reason = reason
        self.hint = hint
        message = reason if not hint else f"{reason} Hint: {hint}"
        super().__init__(message)
