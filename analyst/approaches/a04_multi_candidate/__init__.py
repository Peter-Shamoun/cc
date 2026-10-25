"""A04: three SQL candidates, execution agreement, pairwise selection on disagreement.

Design and the evidence for the expected gain: `research/approaches/a04_multi_candidate.md`.
"""
from .adapter import (
    CONFIG_NAME,
    MultiCandidateAnalyst,
    complete_manifest,
    complete_session,
    configs,
    technique_config,
)
from .backend import ScriptedBackend
from .protocol import run_protocol

__all__ = [
    "CONFIG_NAME",
    "MultiCandidateAnalyst",
    "ScriptedBackend",
    "complete_manifest",
    "complete_session",
    "configs",
    "run_protocol",
    "technique_config",
]
