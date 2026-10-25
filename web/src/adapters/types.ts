import type { AssistantMessage, ID, ISODate, Message, StreamEvent, UserMessage } from '../lib/contract';

/**
 * Thin adapter to the analyst backend. MockAnalystAdapter replays fixtures;
 * HttpAnalystAdapter will call the real endpoint, which runs queries through
 * Cognito -> STS (900 s) -> scoped DuckDB session server-side.
 */
export interface AnalystAdapter {
  readonly name: string;
  streamTurn(input: { conversationId: ID; messages: { id: ID; role: 'user' | 'assistant'; text: string }[]; signal: AbortSignal }): AsyncIterable<StreamEvent>;
  /** Fetch an image/file blob by opaque id (auth header, never a PHI-bearing URL). */
  fetchBlob(id: ID): Promise<Blob>;
}

/** Spec §6.4. The client only ever talks to this interface. */
export interface TranscriptStore {
  readonly mode: 'ephemeral' | 'server-encrypted' | 'references-only';
  listConversations(cursor?: string): Promise<{ items: { id: ID; title: string; updatedAt: ISODate }[]; next?: string }>;
  getConversation(id: ID): Promise<{ id: ID; messages: Message[] } | undefined>;
  appendMessage(conversationId: ID, msg: UserMessage | AssistantMessage, title?: string): Promise<void>;
  /** Replace the last assistant message (regenerate). */
  truncateAfter(conversationId: ID, messageId: ID): Promise<void>;
  deleteConversation(id: ID): Promise<void>;
  setRetention(policy: { days: number }): Promise<void>;
}
