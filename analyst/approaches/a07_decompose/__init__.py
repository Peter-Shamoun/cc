"""A07: decompose a question into checked CTE steps, then submit one query.

The protocol, the prompts, the intermediate checks, and the eval adapter live in
this package. A scripted backend drives the protocol in tests. Nothing here calls
a paid model API.

See ``research/approaches/a07_decompose.md``.
"""

from .adapter import CONFIG_NAME, briefing, configs_document, install_prompts, prepare_run, technique_config
from .protocol import (
    DecompositionResult,
    GuardedDuckDbExecutor,
    Plan,
    PlanRequest,
    ProtocolError,
    RepairRequest,
    Step,
    StepTrace,
    plan_from_json,
    run,
)
from .scripted import ScriptedBackend

__all__ = [
    "CONFIG_NAME",
    "DecompositionResult",
    "GuardedDuckDbExecutor",
    "Plan",
    "PlanRequest",
    "ProtocolError",
    "RepairRequest",
    "ScriptedBackend",
    "Step",
    "StepTrace",
    "briefing",
    "configs_document",
    "install_prompts",
    "plan_from_json",
    "prepare_run",
    "run",
    "technique_config",
]
