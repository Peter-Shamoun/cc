"""A06 explore-first: probe sem.* before the final SQL.

Public entry points:
    run(question, executor, backend)     plan, probe, compress, execute
    prepare_eval_run(...)                bench/eval_subagents.py config plus protocol prompt
"""
from .adapter import (
    APPROACH_ID,
    apply_protocol,
    augment_prompt,
    configs_document,
    prepare_eval_run,
    technique_config,
)
from .protocol import (
    ExplorationResult,
    GuardedExecutor,
    ScriptedBackend,
    executor_for_query_session,
    run,
)

__all__ = [
    "APPROACH_ID",
    "ExplorationResult",
    "GuardedExecutor",
    "ScriptedBackend",
    "apply_protocol",
    "augment_prompt",
    "configs_document",
    "executor_for_query_session",
    "prepare_eval_run",
    "run",
    "technique_config",
]
