import type { AssistantMessage, ID, ISODate, Message, StreamEvent as LegacyStreamEvent, UserMessage } from '../lib/contract';
import type { ChatRequest, StreamEvent as V1StreamEvent } from '../api/contract';

/** Adapters may yield pre-v1 or contract-v1 events; the client reducer (reduceV1) accepts both. */
export type AnyStreamEvent = LegacyStreamEvent | V1StreamEvent;

/**
 * Thin adapter to the analyst backend. MockAnalystAdapter replays fixtures;
 * HttpAnalystAdapter will call the real endpoint, which runs queries through
 * Cognito -> STS (900 s) -> scoped DuckDB session server-side.
 */
/** Transport state of a running stream: 'reconnecting'/'offline' after a drop, 'live' once replay resumes. */
export type ConnectionState = 'live' | 'reconnecting' | 'offline' | 'rate_limited';
/** Extra detail for a connection change; `retryInMs` is set with 'rate_limited' (from Retry-After). */
export interface ConnectionInfo { retryInMs?: number }

export interface AnalystAdapter {
  readonly name: string;
  /**
   * `parentId` on a user message marks an edit (sibling branch, CONTRACT.md §8); `regenerate` re-runs the
   * user turn of an earlier answer so the server records a sibling instead of a duplicate question.
   */
  streamTurn(input: { conversationId: ID; messages: { id: ID; role: 'user' | 'assistant'; text: string; parentId?: ID }[]; signal: AbortSignal; resume?: ChatRequest['resume']; regenerate?: ChatRequest['regenerate']; onConnection?: (state: ConnectionState, info?: ConnectionInfo) => void }): AsyncIterable<AnyStreamEvent>;
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
