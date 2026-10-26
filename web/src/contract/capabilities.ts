/**
 * What the server offers (CONTRACT.md §12). No strategy/model choice since 1.24.0: the server runs one configuration.
 *
 * `loadCapabilities()` asks the server (`GET /api/capabilities`). With no server (mock mode) or on any error it
 * falls back to DEMO_CAPABILITIES, which mirrors the demo backend. capabilities.test.ts keeps it equal to
 * api/contract/examples/capabilities.json (`python -m api.contract.gen_examples`).
 */
import { fetchRetrying, readJsonCapped } from '../lib/fetchGuard';
import { CONTRACT_VERSION } from '../api/contract';
import type { Capabilities, FeedbackRating, FeedbackReason } from './v1.gen';

export const DEMO_CAPABILITIES: Capabilities = {
  contractVersion: CONTRACT_VERSION,
  "demo": true,
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
  "pinArchive": true,
  "temporaryChat": {
    "ttlSeconds": 3600
  },
};

export async function loadCapabilities(base = '', fetchImpl: typeof fetch = (...a) => fetch(...a)): Promise<Capabilities> {
  try {
    // A 429 here would otherwise fall back to the demo capabilities: wait it out first.
    const res = await fetchRetrying(fetchImpl, `${base}/api/capabilities`, { credentials: 'same-origin', cache: 'no-store', headers: { accept: 'application/json' } }, { maxRetries: 1, maxWaitMs: 5000 });
    if (!res.ok) return DEMO_CAPABILITIES;
    const body = await readJsonCapped<Capabilities>(res, 1024 * 1024);
    return typeof body?.contractVersion === 'string' && typeof body.demo === 'boolean' ? body : DEMO_CAPABILITIES;
  } catch {
    return DEMO_CAPABILITIES;
  }
}

/** 1.7.0 (§13): the reason chips for one rating, in server order. Empty when the server predates feedback. */
export const feedbackReasonsFor = (c: Capabilities, rating: FeedbackRating): FeedbackReason[] =>
  (c.feedbackReasons ?? []).filter((r) => r.rating === rating);
