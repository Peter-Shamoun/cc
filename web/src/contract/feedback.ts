/**
 * Answer feedback, thumbs up/down with reasons (contract 1.7.0, CONTRACT.md §13).
 *
 * `PUT /api/conversations/{conversationId}/messages/{messageId}/feedback` with a `FeedbackRequest` sets it (a later
 * PUT replaces it); `DELETE` on the same path clears it. The stored value comes back on `StoredMessage.feedback`.
 * The comment is user text: never log it, and never put it in a URL or localStorage.
 */
import type { Feedback, FeedbackRequest } from './v1.gen';

export const MAX_FEEDBACK_COMMENT = 1000;

export const feedbackUrl = (conversationId: string, messageId: string, base = ''): string =>
  `${base}/api/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/feedback`;

/** Trim the comment, drop it when empty, de-duplicate reasons. Throws on a comment over the limit. */
export function feedbackBody(req: FeedbackRequest): FeedbackRequest {
  const comment = req.comment?.trim();
  if (comment && comment.length > MAX_FEEDBACK_COMMENT) throw new RangeError('comment too long');
  const out: FeedbackRequest = { rating: req.rating, reasons: [...new Set(req.reasons ?? [])] };
  if (comment) out.comment = comment;
  return out;
}

/**
 * Send (`req`) or clear (`null`) feedback. `init` carries auth headers from the caller's adapter.
 * Resolves to the stored `Feedback`, or `null` once cleared; rejects with the HTTP status on failure.
 */
export async function sendFeedback(
  conversationId: string, messageId: string, req: FeedbackRequest | null,
  { base = '', init = {}, fetchImpl = (...a: Parameters<typeof fetch>) => fetch(...a) }:
    { base?: string; init?: RequestInit; fetchImpl?: typeof fetch } = {},
): Promise<Feedback | null> {
  const headers = { ...(init.headers as Record<string, string> | undefined), 'Content-Type': 'application/json' };
  const res = await fetchImpl(feedbackUrl(conversationId, messageId, base), req
    ? { ...init, method: 'PUT', headers, body: JSON.stringify(feedbackBody(req)) }
    : { ...init, method: 'DELETE', headers });
  if (!res.ok) throw new Error(`feedback failed: ${res.status}`);
  return req ? ((await res.json()) as Feedback) : null;
}
