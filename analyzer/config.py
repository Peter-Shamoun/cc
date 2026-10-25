"""Technique flags for the analyst loop.

Screen factors from ``research/technique_factors.yaml`` and ``bench/agent_cli.py``
map onto these fields. ``AnalyzerConfig()`` is the v1 analyst (screen-1 winner
plus execution feedback and verification). ``from_technique`` starts from the
screen's off/baseline levels, then applies the supplied dict, so a screening
config does not silently pick up the v1 defaults.
"""

from __future__ import annotations

from dataclasses import dataclass, fields

HAIKU_MODEL = "claude-haiku-5-5"
SONNET_MODEL = "claude-sonnet-5-5"

_GLOSSARY_ALIASES = {
    "none": "none",
    "basic": "basic",
    "glossary": "basic",
    "pitfalls": "pitfalls",
    "glossary+invariants": "pitfalls",
    "glossary+invariants+pitfalls": "pitfalls",
}
_CONTEXT_STYLES = {"goal", "procedural", "procedural_minimal"}
_EFFORTS = {"default", "low", "medium", "high"}
_SELF_CORRECT = {"none", "errors_only", "errors+result_signals"}
_SELECTION = {"execution_vote", "first_passing", "agree_or_abstain"}
_CASCADES = {"off", "haiku_then_sonnet"}
_REFUSAL = {"minimal", "explicit"}


def _baseline() -> dict:
    """Off levels used by ``from_technique``. Matches the bench session defaults."""
    return dict(
        glossary="basic",
        data_dictionary=False,
        data_profile=False,
        enable_sql=True,
        enable_metrics=False,
        enable_lookup=False,
        enable_examples=False,
        enable_ask=False,
        sql_error_hints=False,
        result_checks=False,
        context_style="goal",
        effort="default",
        max_tool_calls=12,
        max_turns=8,
        max_repairs=2,
        row_limit=1000,
        rows_shown_to_model=20,
        time_limit_seconds=25.0,
        sample_row_limit=5,
        candidates=1,
        candidate_selection="execution_vote",
        cascade="off",
        model=HAIKU_MODEL,
        self_correct="errors_only",
        verify=False,
        prompt_cache=False,
        refusal_prompt="explicit",
        max_tokens=16000,
    )


@dataclass
class AnalyzerConfig:
    """One run of the analyst. See ``docs/agent/README.md`` for the factor map."""

    glossary: str = "pitfalls"
    data_dictionary: bool = True
    data_profile: bool = True
    enable_sql: bool = True
    enable_metrics: bool = False
    enable_lookup: bool = False
    enable_examples: bool = False
    enable_ask: bool = False
    sql_error_hints: bool = True
    result_checks: bool = True
    context_style: str = "procedural"
    effort: str = "low"
    max_tool_calls: int = 12
    max_turns: int = 8
    max_repairs: int = 2
    row_limit: int = 1000
    rows_shown_to_model: int = 20
    time_limit_seconds: float = 25.0
    sample_row_limit: int = 5
    candidates: int = 1
    candidate_selection: str = "execution_vote"
    cascade: str = "off"
    model: str = HAIKU_MODEL
    self_correct: str = "errors+result_signals"
    verify: bool = True
    prompt_cache: bool = False
    refusal_prompt: str = "explicit"
    max_tokens: int = 16000
    cascade_models: tuple[str, ...] = (HAIKU_MODEL, SONNET_MODEL)

    def __post_init__(self):
        self.glossary = _GLOSSARY_ALIASES.get(self.glossary, self.glossary)
        if self.glossary not in {"none", "basic", "pitfalls"}:
            raise ValueError(f"unknown glossary {self.glossary!r}")
        if self.context_style not in _CONTEXT_STYLES:
            raise ValueError(f"unknown context_style {self.context_style!r}")
        if self.effort not in _EFFORTS:
            raise ValueError(f"unknown effort {self.effort!r}")
        if self.self_correct not in _SELF_CORRECT:
            raise ValueError(f"unknown self_correct {self.self_correct!r}")
        if self.candidate_selection not in _SELECTION:
            raise ValueError(f"unknown candidate_selection {self.candidate_selection!r}")
        if self.cascade not in _CASCADES:
            raise ValueError(f"unknown cascade {self.cascade!r}")
        if self.refusal_prompt not in _REFUSAL:
            raise ValueError(f"unknown refusal_prompt {self.refusal_prompt!r}")
        self.candidates = int(self.candidates)
        if not 1 <= self.candidates <= 5:
            raise ValueError("candidates must be from 1 to 5")
        self.max_turns = int(self.max_turns)
        self.max_repairs = int(self.max_repairs)
        self.max_tool_calls = int(self.max_tool_calls)
        self.row_limit = int(self.row_limit)
        self.rows_shown_to_model = int(self.rows_shown_to_model)
        self.sample_row_limit = int(self.sample_row_limit)
        self.max_tokens = int(self.max_tokens)
        self.time_limit_seconds = float(self.time_limit_seconds)
        if self.max_turns < 1 or self.max_repairs < 0 or self.max_tool_calls < 1:
            raise ValueError("turn and tool budgets must be positive")
        if self.row_limit < 1 or self.rows_shown_to_model < 1 or self.time_limit_seconds <= 0:
            raise ValueError("row and time limits must be positive")
        if isinstance(self.cascade_models, list):
            self.cascade_models = tuple(self.cascade_models)
        if self.cascade == "haiku_then_sonnet" and len(self.cascade_models) < 2:
            raise ValueError("haiku_then_sonnet needs two models")

    @classmethod
    def from_technique(cls, technique: dict | None = None, **overrides) -> "AnalyzerConfig":
        """Build a config from a bench technique dict (screening JSON, session.json)."""
        raw = {**_baseline(), **(technique or {}), **overrides}
        known = {f.name for f in fields(cls)}
        unknown = set(raw) - known
        if unknown:
            raise ValueError(f"unknown technique settings: {sorted(unknown)}")
        return cls(**raw)

    def screen_dict(self) -> dict:
        """The subset stored on a bench session. Drops cascade model ids (not a screen knob)."""
        skip = {"cascade_models"}
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in skip}
