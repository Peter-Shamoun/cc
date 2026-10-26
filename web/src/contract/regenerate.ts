/**
 * Regenerate with a style (contract 1.10.0, CONTRACT.md §8.1): "Retry", "More concise" and "More detailed".
 *
 * Each one POSTs /api/chat with `regenerate: {messageId, style}` and `messages: []`. The reply is a new assistant
 * message with `regenerateOf` = the old id, so it's a sibling: show it in place as the next version ("2 / 2") and
 * keep the old one reachable with the version arrows. Don't send a follow-up user turn.
 */
import type { ChatRequest, RegenerateStyle } from '../api/contract';

export const REGENERATE_STYLES: readonly { style: RegenerateStyle; label: string }[] = [
  { style: 'default', label: 'Retry' },
  { style: 'concise', label: 'More concise' },
  { style: 'detailed', label: 'More detailed' },
];

/**
 * The /api/chat body for regenerating `messageId`. `default` sends no style (a plain re-run, understood by any
 * 1.x server). `options` (strategy/model) carry over when given.
 */
export function regenerateRequest(
  conversationId: string, messageId: string, style: RegenerateStyle = 'default', options?: ChatRequest['options'],
): ChatRequest {
  const req: ChatRequest = {
    conversationId, messages: [], regenerate: style === 'default' ? { messageId } : { messageId, style },
  };
  if (options) req.options = options;
  return req;
}
