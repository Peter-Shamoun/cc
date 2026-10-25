"""Screen-2 arm definitions for this variant.

``bench.eval_subagents`` rejects technique keys it does not already know, and this PR does not
edit that harness (other design PRs share it). The main dev runs an arm in one of two ways:

1. In-process, once an LLM adapter is passed in::

       CC_MC_ARM=mc_n3_majority_then_pairwise \\
         python -m bench.eval run --agent analyst.variants.multi_candidate.agent:MultiCandidateAgent

   The agent reads ``CC_MC_ARM`` (or ``CC_MC_CONFIG``, a JSON object of the same flags).
   Without an injected LLM it fails on the first question rather than calling an API.

2. Subagents, later: prepare N sessions per question with ``prompts.subagent_prompt``, one strategy
   each, re-execute each submitted SQL, then call ``selector.select``. The prompts and ``TOOL_SPEC``
   are the contract for that wiring. Do not turn temperature on; the API rejects it.

The arm to run against the stage-1 winner is ``mc_n3_majority_then_pairwise``. Keep repair, effort,
glossary, and the typed-metrics lane off in that arm so the contrast is only N and the selector.
``mc_n1_direct`` is the paired baseline. ``mc_n5_majority_then_pairwise`` is the cost check, not a
second accuracy claim, until N=3 shows an upper bound at least a couple of points above the single
candidate.
"""
from __future__ import annotations

STRATEGY_N3 = ["direct", "grain_and_date", "filter_then_aggregate"]
STRATEGY_N5 = STRATEGY_N3 + ["unit_of_analysis", "literal_projection"]

# Flags that are not bench.agent_cli keys. Listed so a harness change can allow them by name.
CONFIG_FLAGS = (
    "n_candidates",
    "strategies",
    "selection",
    "selector_model",
    "generator_model",
    "temperature",
    "repair_on_error",
    "max_turns_per_candidate",
    "refusal_policy",
    "min_successes",
    "effort",
    "glossary",
    "context_style",
    "cache",
    "exploratory_sql",
    "cluster",
)

_BASE = {
    "temperature": None,
    "repair_on_error": 0,
    "max_turns_per_candidate": 4,
    "refusal_policy": "any_veto",
    "min_successes": 1,
    "effort": "low",
    "glossary": "basic",
    "context_style": "goal",
    "cache": "warm_prefix_then_parallel",
    "exploratory_sql": True,
    "selector_model": "claude-sonnet-5-5",
    "generator_model": "arm_model",
    "cluster": {
        "tol": "from_question_text",
        "allow_percent": "if_question_mentions_rate",
        "allow_sign_flip": "if_question_says_positive",
        "topn": "if_question_asks_for_a_top_n",
        "metric_col": "auto",
    },
}

SCREEN_ARMS = {
    "mc_n1_direct": {
        **_BASE,
        "n_candidates": 1,
        "strategies": ["direct"],
        "selection": "majority",
    },
    "mc_n3_majority": {
        **_BASE,
        "n_candidates": 3,
        "strategies": list(STRATEGY_N3),
        "selection": "majority",
    },
    "mc_n3_majority_then_pairwise": {
        **_BASE,
        "n_candidates": 3,
        "strategies": list(STRATEGY_N3),
        "selection": "majority_then_pairwise",
    },
    "mc_n3_pairwise_always": {
        **_BASE,
        "n_candidates": 3,
        "strategies": list(STRATEGY_N3),
        "selection": "pairwise",
    },
    "mc_n3_verifier": {
        **_BASE,
        "n_candidates": 3,
        "strategies": list(STRATEGY_N3),
        "selection": "verifier",
    },
    "mc_n3_agree_or_abstain": {
        **_BASE,
        "n_candidates": 3,
        "strategies": list(STRATEGY_N3),
        "selection": "agree_or_abstain",
    },
    "mc_n5_majority_then_pairwise": {
        **_BASE,
        "n_candidates": 5,
        "strategies": list(STRATEGY_N5),
        "selection": "majority_then_pairwise",
    },
}


def arm(name: str) -> dict:
    if name not in SCREEN_ARMS:
        raise KeyError(f"unknown arm {name!r}; known: {sorted(SCREEN_ARMS)}")
    return dict(SCREEN_ARMS[name])
