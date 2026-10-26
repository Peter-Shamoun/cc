"""What this server offers a client (``GET /api/capabilities``).

There is no strategy or model choice (removed in contract 1.24.0): the server runs one shipped analyzer configuration,
fixed in code and server config, never chosen by a request.
"""
from __future__ import annotations

from api.feedback import REASONS as FEEDBACK_REASONS

#: ``ChatRequest.options`` keys that used to pick a strategy/model (contract 1.4.0–1.23.0). Sending one is a 422.
REMOVED_RUN_OPTION_KEYS = ("strategy", "model")


def capabilities(contract_version: str, demo: bool, sql_literal_masking: str = "phi",
                 temporary_ttl_seconds: float | None = None) -> dict:
    """The ``Capabilities`` body. No user or organization data."""
    return {"contractVersion": contract_version, "demo": demo,
            "feedbackReasons": [dict(r) for r in FEEDBACK_REASONS],
            "sqlLiteralMasking": sql_literal_masking if sql_literal_masking in ("phi", "all", "off") else "phi",
            "pinArchive": True,  # 1.17.0: PATCH pinned/archived persists server-side
            # 1.20.0: ChatRequest.temporary is honoured (never saved or listed; §11.1)
            **({"temporaryChat": {"ttlSeconds": max(1, round(temporary_ttl_seconds))}}
               if temporary_ttl_seconds and temporary_ttl_seconds > 0 else {})}
