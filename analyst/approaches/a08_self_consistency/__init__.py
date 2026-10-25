"""A08 self-consistency: five independent attempts, one voted answer, one confidence.

Public surface:
    protocol   — N=5, the technique config `bench.eval_subagents` can prepare
    normalize  — column order, rounding, ties
    vote       — majority, vote margin, abstain / escalate
    adapter    — turn a finished subagent run into that vote
"""

from .protocol import (
    ACCEPT_MARGIN,
    APPROACH_ID,
    ATTEMPT_COUNT,
    CONFIG_NAME,
    ESCALATE_MARGIN,
    base_technique_config,
    configs_for_eval_subagents,
    cost_multiplier,
)
from .vote import decide

__all__ = [
    "ACCEPT_MARGIN",
    "APPROACH_ID",
    "ATTEMPT_COUNT",
    "CONFIG_NAME",
    "ESCALATE_MARGIN",
    "base_technique_config",
    "configs_for_eval_subagents",
    "cost_multiplier",
    "decide",
]
