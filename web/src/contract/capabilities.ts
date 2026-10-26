/**
 * Strategies and models for a picker (contract 1.4.0, CONTRACT.md §12).
 *
 * `loadCapabilities()` asks the server (`GET /api/capabilities`). With no server (mock mode) or on any error it
 * falls back to DEMO_CAPABILITIES, which mirrors the demo backend. capabilities.test.ts keeps it equal to
 * api/contract/examples/capabilities.json (`python -m api.contract.gen_examples`).
 */
import { CONTRACT_VERSION } from '../api/contract';
import type { Capabilities, FeedbackRating, FeedbackReason, ModelOption, StrategyOption } from './v1.gen';

export const DEMO_CAPABILITIES: Capabilities = {
  contractVersion: CONTRACT_VERSION,
  "demo": true,
  "scope": "public",
  "defaults": {
    "strategy": "tool_loop",
    "model": "auto"
  },
  "strategies": [
    {
      "id": "tool_loop",
      "label": "Standard",
      "description": "One model explores the data, writes and checks SQL, and repairs it if a query fails.",
      "costTier": "low",
      "latencyTier": "fast",
      "models": [
        "auto",
        "claude-haiku-5-5",
        "claude-sonnet-5-5"
      ],
      "default": true,
      "available": true
    },
    {
      "id": "cascade",
      "label": "Escalate when stuck",
      "description": "Starts on a small, fast model and hands over to a larger one if it can't produce a verified answer.",
      "costTier": "medium",
      "latencyTier": "medium",
      "models": [
        "auto"
      ],
      "default": false,
      "available": true
    },
    {
      "id": "candidates",
      "label": "Cross-check",
      "description": "Writes several candidate queries, runs them all, and answers with the result they agree on.",
      "costTier": "high",
      "latencyTier": "slow",
      "models": [
        "auto",
        "claude-sonnet-5-5"
      ],
      "default": false,
      "available": true
    }
  ],
  "models": [
    {
      "id": "auto",
      "label": "Auto",
      "description": "The server picks the model for the strategy.",
      "costTier": "low",
      "default": true,
      "available": true
    },
    {
      "id": "claude-haiku-5-5",
      "label": "Haiku",
      "description": "Small and fast.",
      "tier": "small",
      "provider": "anthropic",
      "costTier": "low",
      "default": false,
      "available": true
    },
    {
      "id": "claude-sonnet-5-5",
      "label": "Sonnet",
      "description": "Larger; better on hard questions.",
      "tier": "medium",
      "provider": "anthropic",
      "costTier": "medium",
      "default": false,
      "available": true
    }
  ],
  "feedbackReasons": [
    {
      "id": "correct",
      "label": "Correct",
      "rating": "up"
    },
    {
      "id": "clear",
      "label": "Easy to understand",
      "rating": "up"
    },
    {
      "id": "fast",
      "label": "Fast",
      "rating": "up"
    },
    {
      "id": "wrong_numbers",
      "label": "Numbers look wrong",
      "rating": "down"
    },
    {
      "id": "wrong_query",
      "label": "Wrong filters or SQL",
      "rating": "down"
    },
    {
      "id": "misunderstood",
      "label": "Misunderstood the question",
      "rating": "down"
    },
    {
      "id": "missing_data",
      "label": "Missing data",
      "rating": "down"
    },
    {
      "id": "too_slow",
      "label": "Too slow",
      "rating": "down"
    },
    {
      "id": "other",
      "label": "Something else",
      "rating": "down"
    }
  ],
  "sqlLiteralMasking": "phi",
  "shareExpiry": { "options": [7, 30, 90], "default": 30 },
  "pinArchive": true,
};

export async function loadCapabilities(base = '', fetchImpl: typeof fetch = (...a) => fetch(...a)): Promise<Capabilities> {
  try {
    const res = await fetchImpl(`${base}/api/capabilities`, { credentials: 'same-origin', cache: 'no-store', headers: { accept: 'application/json' } });
    if (!res.ok) return DEMO_CAPABILITIES;
    const body = (await res.json()) as Capabilities;
    return Array.isArray(body?.strategies) && Array.isArray(body?.models) ? body : DEMO_CAPABILITIES;
  } catch {
    return DEMO_CAPABILITIES;
  }
}

export const defaultStrategy = (c: Capabilities): StrategyOption | undefined =>
  c.strategies.find((s) => s.id === c.defaults.strategy) ?? c.strategies.find((s) => s.default);

/** Models a picker may offer once `strategyId` is chosen (in the server's order). */
export const modelsFor = (c: Capabilities, strategyId: string): ModelOption[] => {
  const allowed = new Set(c.strategies.find((s) => s.id === strategyId)?.models ?? []);
  return c.models.filter((m) => allowed.has(m.id));
};

/** `ChatRequest.options` for a choice; omits defaults so the request stays minimal. */
export function runOptions(c: Capabilities, strategy?: string, model?: string): { strategy?: string; model?: string } {
  const out: { strategy?: string; model?: string } = {};
  if (strategy && strategy !== c.defaults.strategy) out.strategy = strategy;
  if (model && model !== c.defaults.model) out.model = model;
  return out;
}

/** 1.7.0 (§13): the reason chips for one rating, in server order. Empty when the server predates feedback. */
export const feedbackReasonsFor = (c: Capabilities, rating: FeedbackRating): FeedbackReason[] =>
  (c.feedbackReasons ?? []).filter((r) => r.rating === rating);
