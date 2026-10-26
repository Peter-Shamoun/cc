"""What the server can run: answer strategies and models for a picker (contract 1.4.0, ``GET /api/capabilities``).

Demo values. Each strategy maps onto real ``AnalyzerConfig`` overrides (the same ones the demo scenarios use), so the
real analyzer can plug in by reading ``config`` here. In demo mode (``ScriptedLLM``) the chosen strategy is validated
and echoed on ``message.start.strategy``, but the scripted scenario still drives the run. No paid API is ever called.
"""
from __future__ import annotations

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

STRATEGY_IDS = frozenset(s["id"] for s in STRATEGIES)
MODEL_IDS = frozenset(m["id"] for m in MODELS)


def capabilities(contract_version: str, demo: bool) -> dict:
    """The ``Capabilities`` body. ``config`` is server-internal and never sent."""
    strategies = [{k: v for k, v in s.items() if k != "config"} | {"default": s["id"] == DEFAULT_STRATEGY,
                                                                    "available": True} for s in STRATEGIES]
    models = [{k: v for k, v in m.items() if v is not None} | {"default": m["id"] == DEFAULT_MODEL}
              for m in MODELS]
    return {"contractVersion": contract_version, "demo": demo, "defaults": {"strategy": DEFAULT_STRATEGY,
            "model": DEFAULT_MODEL}, "strategies": strategies, "models": models}


def check_options(options: dict) -> str | None:
    """None if ``options.strategy`` / ``options.model`` are known and compatible, else a user-facing message."""
    strategy = options.get("strategy", DEFAULT_STRATEGY)
    model = options.get("model", DEFAULT_MODEL)
    if strategy not in STRATEGY_IDS:
        return "Unknown strategy."
    if model not in MODEL_IDS:
        return "Unknown model."
    allowed = next(s["models"] for s in STRATEGIES if s["id"] == strategy)
    if model not in allowed:
        return "That model isn't available for this strategy."
    return None
