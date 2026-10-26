/**
 * Answer feedback (thumbs + reasons). Held in memory for the UI; the live adapter registers a sink
 * (`setFeedbackSink`) that PUTs/DELETEs it per contract 1.7.0 (CONTRACT.md §13, src/contract/feedback.ts).
 * Nothing is written to localStorage/sessionStorage, logged, or put in a URL: the comment is user text.
 */
import { DEMO_CAPABILITIES, feedbackReasonsFor } from '../contract/capabilities';
import type { Capabilities, Feedback, FeedbackReason, FeedbackRequest } from '../contract/v1.gen';

export type Vote = 'up' | 'down';
export interface AnswerFeedback { messageId: string; vote: Vote | null; reasons?: string[]; detail?: string; at: number }
type Sink = (f: AnswerFeedback) => void | Promise<void>;

const mem = new Map<string, AnswerFeedback>();
let sink: Sink | null = null;

export function setFeedbackSink(s: Sink | null): void { sink = s; }
export function feedbackFor(messageId: string): AnswerFeedback | undefined { return mem.get(messageId); }
export function recordFeedback(f: Omit<AnswerFeedback, 'at'>): AnswerFeedback {
  const prev = mem.get(f.messageId);
  // A new vote drops reasons given for the old one; reasons/detail ride along with the current vote.
  const next: AnswerFeedback = { ...(prev && prev.vote === f.vote ? prev : {}), ...f, at: Date.now() };
  if (next.vote !== 'down') { delete next.reasons; delete next.detail; }
  mem.set(f.messageId, next);
  void Promise.resolve(sink?.(next)).catch(() => undefined);
  return next;
}
export function clearFeedbackForTests(): void { mem.clear(); sink = null; caps = DEMO_CAPABILITIES; }

/** The contract body for a vote (`null` = clear it: DELETE). Reasons are Capabilities.feedbackReasons ids. */
export function toFeedbackRequest(f: AnswerFeedback): FeedbackRequest | null {
  if (!f.vote) return null;
  const req: FeedbackRequest = { rating: f.vote, reasons: f.reasons ?? [] };
  const comment = f.detail?.trim();
  if (comment) req.comment = comment;
  return req;
}

/** Reason chips come from the server's capabilities (ids stable, labels shown); demo list until loaded. */
let caps: Capabilities = DEMO_CAPABILITIES;
export function setFeedbackCapabilities(c: Capabilities): void { caps = c; }
export function feedbackReasons(rating: Vote): FeedbackReason[] { return feedbackReasonsFor(caps, rating); }

/** The vote a stored answer came back with: `StoredMessage.feedback` (1.7.0 object) or a legacy 'up'/'down'. */
export function storedVote(stored: unknown): Vote | null {
  if (stored === 'up' || stored === 'down') return stored;
  const r = (stored as Feedback | undefined)?.rating;
  return r === 'up' || r === 'down' ? r : null;
}

/** SQL behind an answer for the clipboard: one statement as-is, several numbered with their purpose. */
// Contract 1.14.0 §10: masked SQL is for reading. The copy says so, so nobody pastes `?` placeholders into a console expecting the run's result.
export const maskComment = (n: number) => `-- Patient values hidden: ${n} ${n === 1 ? 'value is' : 'values are'} shown as ?. Not runnable as-is.`;
export function sqlForClipboard(queries: { label: string; sql: string; state?: string; maskedLiterals?: number }[]): string {
  const ok = queries.filter((q) => q.state !== 'error' && q.sql.trim());
  const note = (q: (typeof ok)[number]) => (q.maskedLiterals && q.maskedLiterals > 0 ? `${maskComment(q.maskedLiterals)}\n` : '');
  if (ok.length === 1) return `${note(ok[0])}${ok[0].sql.trim()}`;
  return ok.map((q, i) => `-- ${i + 1}. ${q.label.replace(/\s+/g, ' ')}\n${note(q)}${q.sql.trim().replace(/;?\s*$/, ';')}`).join('\n\n');
}
