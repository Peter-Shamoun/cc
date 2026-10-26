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
  listConversations(cursor?: string): Promise<{ items: ThreadSummary[]; next?: string }>;
  getConversation(id: ID): Promise<{ id: ID; messages: Message[] } | undefined>;
  appendMessage(conversationId: ID, msg: UserMessage | AssistantMessage, title?: string): Promise<void>;
  /** Replace the last assistant message (regenerate). */
  truncateAfter(conversationId: ID, messageId: ID): Promise<void>;
  deleteConversation(id: ID): Promise<void>;
  /** Rename a thread (user-chosen titles are the user's responsibility; generated ones are scrubbed). */
  renameConversation(id: ID, title: string): Promise<void>;
  /** Replace the whole thread (edit-and-branch switches the visible branch). */
  replaceMessages(conversationId: ID, msgs: (UserMessage | AssistantMessage)[]): Promise<void>;
  setRetention(policy: { days: number }): Promise<void>;
  /** Pin/unpin a thread to the top of the sidebar (optional; UI hides the action if absent). */
  setPinned?(id: ID, pinned: boolean): Promise<void>;
  /**
   * Full-text search across titles AND message text. Runs where the transcripts live (memory here,
   * server-side for an encrypted store), so the query never goes to a URL, log or browser storage.
   */
  search?(query: string, limit?: number): Promise<SearchHit[]>;
}

export interface ThreadSummary { id: ID; title: string; updatedAt: ISODate; pinned?: boolean }
/** One search result: the thread, the matched message (if not the title) and a short snippet around the match. */
export interface SearchHit { id: ID; title: string; updatedAt: ISODate; messageId?: ID; snippet: string; matchStart: number; matchLength: number }
