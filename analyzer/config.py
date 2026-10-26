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
    "invariants": "invariants",
    "pitfalls": "pitfalls",
    "glossary+invariants": "pitfalls",
    "glossary+invariants+pitfalls": "pitfalls",
}
GLOSSARY_LEVELS = {"none", "basic", "invariants", "pitfalls"}
_CONTEXT_STYLES = {"goal", "procedural", "procedural_minimal", "minimal"}
_EFFORTS = {"default", "low", "medium", "high"}
_SELF_CORRECT = {"none", "errors_only", "errors+result_signals"}
_SELECTION = {"execution_vote", "first_passing", "agree_or_abstain"}
_CASCADES = {"off", "haiku_then_sonnet"}
_REFUSAL = {"minimal", "explicit"}
_DICTIONARY_DETAIL = {"descriptions", "derivations"}
_PROFILE_DETAIL = {"full", "enums"}
GENERATION_MODES = {"free_sql", "decompose_cte", "compiler_sql_fallback", "compiler_decompose_fallback"}
PLAN_MODES = {"none", "plan", "plan_and_replan", "external_plan"}
CANDIDATE_SHAPES = {"none", "direct", "query_plan", "divide_and_conquer"}

# Levels that exist only on the Claude Code subagent route (analyzer.cli + analyzer.harness). The in-process loop
# refuses a config that sets any of them away from its off level instead of silently ignoring it.
SUBAGENT_ROUTE_OFF_LEVELS = dict(
    playbook=False,
    dictionary_detail="descriptions",
    profile_detail="full",
    lookup_mandatory_before_literals=False,
    generation_mode="free_sql",
    explore_probes=False,
    plan_mode="none",
    state_assumptions=False,
    restate_question=False,
    actionable_errors=False,
    digest_results=False,
    rewritten_tool_descriptions=False,
    domain_lint=False,
    reconciliation_checks=False,
    report_confidence=False,
    candidate_shape="none",
    orchestration_bundle=None,
)


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
        examples_k=3,
        **SUBAGENT_ROUTE_OFF_LEVELS,
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
    examples_k: int = 3
    playbook: bool = False
    dictionary_detail: str = "descriptions"
    profile_detail: str = "full"
    lookup_mandatory_before_literals: bool = False
    generation_mode: str = "free_sql"
    explore_probes: bool = False
    plan_mode: str = "none"
    state_assumptions: bool = False
    restate_question: bool = False
    actionable_errors: bool = False
    digest_results: bool = False
    rewritten_tool_descriptions: bool = False
    domain_lint: bool = False
    reconciliation_checks: bool = False
    report_confidence: bool = False
    candidate_shape: str = "none"
    orchestration_bundle: str | None = None
    cascade_models: tuple[str, ...] = (HAIKU_MODEL, SONNET_MODEL)

    def __post_init__(self):
        self.glossary = _GLOSSARY_ALIASES.get(self.glossary, self.glossary)
        if self.glossary not in GLOSSARY_LEVELS:
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
        if self.dictionary_detail not in _DICTIONARY_DETAIL:
            raise ValueError(f"unknown dictionary_detail {self.dictionary_detail!r}")
        if self.profile_detail not in _PROFILE_DETAIL:
            raise ValueError(f"unknown profile_detail {self.profile_detail!r}")
        if self.generation_mode not in GENERATION_MODES:
            raise ValueError(f"unknown generation_mode {self.generation_mode!r}")
        if self.plan_mode not in PLAN_MODES:
            raise ValueError(f"unknown plan_mode {self.plan_mode!r}")
        if self.candidate_shape not in CANDIDATE_SHAPES:
            raise ValueError(f"unknown candidate_shape {self.candidate_shape!r}")
        if self.lookup_mandatory_before_literals and not self.enable_lookup:
            raise ValueError("lookup_mandatory_before_literals needs enable_lookup")
        self.examples_k = int(self.examples_k)
        if self.enable_examples and not 1 <= self.examples_k <= 10:
            raise ValueError("examples_k must be from 1 to 10")
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

    def subagent_only_levels_in_use(self) -> list[str]:
        """Settings this config moves off their off level that only the subagent route implements."""
        return sorted(name for name, off in SUBAGENT_ROUTE_OFF_LEVELS.items() if getattr(self, name) != off)

    def interpretation_required(self) -> bool:
        """Any of #10, #11, #25 on: an interpret record must precede sql, spec and answer."""
        return self.plan_mode != "none" or self.state_assumptions or self.restate_question

    def screen_dict(self) -> dict:
        """The subset stored on a bench session. Drops cascade model ids (not a screen knob)."""
        skip = {"cascade_models"}
        return {f.name: getattr(self, f.name) for f in fields(self) if f.name not in skip}
