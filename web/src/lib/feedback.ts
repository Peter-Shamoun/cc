/**
 * Answer feedback (thumbs + reasons), memory only until the contract team's feedback shape lands.
 * Nothing is written to localStorage/sessionStorage: the free-text detail could hold identifiers.
 * When the contract adds an endpoint, register it with `setFeedbackSink` (adapter wiring only).
 */
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
export function clearFeedbackForTests(): void { mem.clear(); sink = null; }

/** SQL behind an answer for the clipboard: one statement as-is, several numbered with their purpose. */
export function sqlForClipboard(queries: { label: string; sql: string; state?: string }[]): string {
  const ok = queries.filter((q) => q.state !== 'error' && q.sql.trim());
  if (ok.length === 1) return ok[0].sql.trim();
  return ok.map((q, i) => `-- ${i + 1}. ${q.label.replace(/\s+/g, ' ')}\n${q.sql.trim().replace(/;?\s*$/, ';')}`).join('\n\n');
}
