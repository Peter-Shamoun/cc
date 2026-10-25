"""A02: one model, a fixed tool list, and a repair budget enforced in code.

The model explores the schema, samples rows, and drafts SQL. This package executes
every draft through the guarded read path and allows at most two repairs when the
draft errors, returns no rows, or trips a programmatic suspicion check.
"""

from .adapter import A02ToolLoopAgent, CONFIG_NAME, TECHNIQUE_CONFIG, configs_document
from .protocol import MAX_REPAIRS, run_question

__all__ = [
    "A02ToolLoopAgent",
    "CONFIG_NAME",
    "MAX_REPAIRS",
    "TECHNIQUE_CONFIG",
    "configs_document",
    "run_question",
]
