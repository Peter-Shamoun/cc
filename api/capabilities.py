"""What the server can run: answer strategies and models for a picker (contract 1.4.0, ``GET /api/capabilities``).

Demo values. Each strategy maps onto real ``AnalyzerConfig`` overrides (the same ones the demo scenarios use), so the
real analyzer can plug in by reading ``config`` here. In demo mode (``ScriptedLLM``) the chosen strategy is validated
and echoed on ``message.start.strategy``, but the scripted scenario still drives the run. No paid API is ever called.
"""
from __future__ import annotations

import json
import os

from api.share import DEFAULT_EXPIRY_DAYS as SHARE_DEFAULT_EXPIRY_DAYS, EXPIRY_DAYS as SHARE_EXPIRY_DAYS
from api.feedback import REASONS as FEEDBACK_REASONS

DEFAULT_STRATEGY = "tool_loop"
DEFAULT_MODEL = "auto"

STRATEGIES: list[dict] = [
    {"id": "tool_loop", "label": "Standard",
     "description": "One model explores the data, writes and checks SQL, and repairs it if a query fails.",
     "costTier": "low", "latencyTier": "fast", "models": ["auto", "claude-haiku-5-5", "claude-sonnet-5-5"],
     "config": {}},
    {"id": "cascade", "label": "Escalate when stuck",
     "description": "Starts on a small, fast model and hands over to a larger one if it can't produce a verified answer.",
     "costTier": "medium", "latencyTier": "medium", "models": ["auto"],
     "config": {"cascade": "haiku_then_sonnet"}},
    {"id": "candidates", "label": "Cross-check",
     "description": "Writes several candidate queries, runs them all, and answers with the result they agree on.",
     "costTier": "high", "latencyTier": "slow", "models": ["auto", "claude-sonnet-5-5"],
     "config": {"candidates": 3, "candidate_selection": "execution_vote"}},
]

MODELS: list[dict] = [
    {"id": "auto", "label": "Auto", "description": "The server picks the model for the strategy.", "tier": None,
     "costTier": "low"},
    {"id": "claude-haiku-5-5", "label": "Haiku", "description": "Small and fast.", "tier": "small",
     "provider": "anthropic", "costTier": "low"},
    {"id": "claude-sonnet-5-5", "label": "Sonnet", "description": "Larger; better on hard questions.",
     "tier": "medium", "provider": "anthropic", "costTier": "medium"},
]

#: Per-organization availability (demo values; prod reads the org's plan/BAA settings). Keyed by Principal.customer.
#: Override with CC_API_ORG_POLICY (JSON of the same shape). Unlisted orgs get everything.
ORG_POLICY: dict[str, dict] = {
    "acme_diagnostics": {
        "strategies": {"candidates": "Cross-check isn't enabled for your organization."},
        "models": {"claude-sonnet-5-5": "Sonnet isn't enabled for your organization."},
    },
}

STRATEGY_IDS = frozenset(s["id"] for s in STRATEGIES)
MODEL_IDS = frozenset(m["id"] for m in MODELS)


def org_policy(customer: str | None) -> dict:
    raw = os.environ.get("CC_API_ORG_POLICY")
    policy = json.loads(raw) if raw else ORG_POLICY
    return policy.get(customer or "", {}) if customer else {}


def capabilities(contract_version: str, demo: bool, customer: str | None = None,
                 sql_literal_masking: str = "phi") -> dict:
    """The ``Capabilities`` body for ``customer`` (None: signed out, nothing org-specific). ``config`` never leaves."""
    policy = org_policy(customer)
    off_s, off_m = policy.get("strategies", {}), policy.get("models", {})

    def flag(item: dict, off: dict) -> dict:
        return {"available": False, "unavailableReason": off[item["id"]]} if item["id"] in off else {"available": True}

    strategies = [{k: v for k, v in s.items() if k != "config"} | {"default": s["id"] == DEFAULT_STRATEGY}
                  | flag(s, off_s) for s in STRATEGIES]
    models = [{k: v for k, v in m.items() if v is not None} | {"default": m["id"] == DEFAULT_MODEL} | flag(m, off_m)
              for m in MODELS]
    return {"contractVersion": contract_version, "demo": demo, "scope": "organization" if customer else "public",
            "defaults": {"strategy": DEFAULT_STRATEGY, "model": DEFAULT_MODEL}, "strategies": strategies,
            "models": models, "feedbackReasons": [dict(r) for r in FEEDBACK_REASONS],
            "sqlLiteralMasking": sql_literal_masking if sql_literal_masking in ("phi", "all", "off") else "phi",
            "shareExpiry": {"options": list(SHARE_EXPIRY_DAYS), "default": SHARE_DEFAULT_EXPIRY_DAYS},
            "pinArchive": True}  # 1.17.0: PATCH pinned/archived persists server-side


def check_options(options: dict, customer: str | None = None) -> str | None:
    """None if ``options.strategy`` / ``options.model`` are known, compatible and enabled for the org."""
    strategy = options.get("strategy", DEFAULT_STRATEGY)
    model = options.get("model", DEFAULT_MODEL)
    if strategy not in STRATEGY_IDS:
        return "Unknown strategy."
    if model not in MODEL_IDS:
        return "Unknown model."
    allowed = next(s["models"] for s in STRATEGIES if s["id"] == strategy)
    if model not in allowed:
        return "That model isn't available for this strategy."
    policy = org_policy(customer)
    return policy.get("strategies", {}).get(strategy) or policy.get("models", {}).get(model)
