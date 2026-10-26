import type { ChatRequest } from '../api/contract';
import type { ViewMessage } from './useChat';
import { useExpired } from './useExpired';

type Clarification = { clarificationId: string; question: string; expiresAt?: string | null };

/**
 * QA-A30: the clarifying question the next composer send answers, as in Claude and ChatGPT, where the chat box is
 * the reply. It must be on the last finished answer, not yet answered, and not a "Response interrupted" stub.
 */
export function openClarification(msgs: readonly ViewMessage[]): Clarification | null {
  const last = msgs[msgs.length - 1];
  if (!last || last.role !== 'assistant' || last.streaming || ('interrupted' in last && last.interrupted)) return null;
  for (const p of last.parts) {
    if (p.type === 'clarification' && !p.answered) return p as unknown as Clarification;
  }
  return null;
}

/** The open clarification, or null once it expires (server clock; the card flips to "expired" at the same moment). */
export function useOpenClarification(msgs: readonly ViewMessage[], busy: boolean): Clarification | null {
  const open = busy ? null : openClarification(msgs);
  const expired = useExpired(open?.expiresAt);
  return open && !expired ? open : null;
}

/** The resume body for a composer reply: free text only (choices stay on the card's chips). */
export const composerResume = (c: Clarification, text: string): NonNullable<ChatRequest['resume']> => ({ clarificationId: c.clarificationId, text: text.trim() });
