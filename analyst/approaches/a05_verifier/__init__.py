"""A05: plan and SQL, execute through the guarded sem.* path, then a fresh-context critic.

Revise once if the critic objects. See research/approaches/a05_verifier.md.
"""

from analyst.approaches.a05_verifier.adapter import (
    CONFIG_NAME,
    TECHNIQUE_CONFIG,
    configs,
    install_protocol,
    prepare,
    run_manifest,
    run_session,
)
from analyst.approaches.a05_verifier.protocol import run_protocol

__all__ = [
    "CONFIG_NAME",
    "TECHNIQUE_CONFIG",
    "configs",
    "install_protocol",
    "prepare",
    "run_manifest",
    "run_protocol",
    "run_session",
]
