"""A09 assumptions-first analyst.

Before any SQL, list the readings that would change the number, lock the one a
revenue-cycle analyst would use, and say so with the answer. A product UI asks
only when a listed fork is still open. The benchmark adapter does not ask.
"""

from .adapter import (
    CONFIG_NAME,
    TECHNIQUE_CONFIG,
    apply_approach_to_manifest,
    augment_prompt,
    prepare_run,
)
from .checklist import analyze, assumption_sentences, sql_violations
from .protocol import AgentResult, Protocol, ProtocolError, run
from .query_gateway import QueryGateway, gateway_from_query_session, open_secure_gateway

__all__ = [
    "CONFIG_NAME",
    "TECHNIQUE_CONFIG",
    "AgentResult",
    "Protocol",
    "ProtocolError",
    "QueryGateway",
    "analyze",
    "apply_approach_to_manifest",
    "assumption_sentences",
    "augment_prompt",
    "gateway_from_query_session",
    "open_secure_gateway",
    "prepare_run",
    "run",
    "sql_violations",
]
