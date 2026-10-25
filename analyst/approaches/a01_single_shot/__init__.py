"""A01 single-shot baseline.

One SQL statement, written from the question plus the basic glossary and the data dictionary,
executed once. That execution is the answer. No repair and no exploration.
"""

from .adapter import (
    APPROACH_ID,
    as_eval_configs,
    drive_session,
    prepare_run,
    technique_config,
)
from .protocol import run_single_shot

__all__ = [
    "APPROACH_ID",
    "as_eval_configs",
    "drive_session",
    "prepare_run",
    "run_single_shot",
    "technique_config",
]
