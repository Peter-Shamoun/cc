import type { AssistantMessage, ID, ISODate, Message, UserMessage } from '../lib/contract';
import type { SearchHit, ThreadSummary, TranscriptStore } from './types';
import { ConversationsApi, ConversationsApiError } from './conversationsApi';
import { newAssistantViewV1, reduceV1, type FinishReason, type StreamEvent } from '../api/contract';

/**
 * The server persists an answer's step events flat (`steps: StepEvent[]`, api/app.py); the answer UI reads
 * `steps: StepView[]` (grouped by stepId). Replay the stored events through the contract reducer so a reloaded
 * chat renders exactly like the live one.
 */
export function hydrate(m: Message): Message {
  const raw = (m as { steps?: unknown }).steps;
  if (m.role !== 'assistant' || !Array.isArray(raw) || raw.length === 0 || !raw.every((e) => e && typeof e === 'object' && 'type' in e)) return m;
  let v = newAssistantViewV1(m.id);
  for (const ev of raw as StreamEvent[]) v = reduceV1(v, ev);
  const finishReason = ((m as { finishReason?: FinishReason }).finishReason ?? 'stop') as FinishReason;
  v = reduceV1(v, { type: 'message.end', messageId: m.id, finishReason, usage: (m as { usage?: never }).usage ?? { queries: 0, elapsedMs: 0 } } as StreamEvent);
  const esc = (raw as { type: string }[]).filter((e) => e.type === 'escalation');
  return { ...m, steps: v.steps, escalations: v.escalations.length ? v.escalations : esc } as unknown as Message;
}

/** The server's generic title (api/store.py DEFAULT_TITLE): it never echoes the first question. */
const SERVER_DEFAULT_TITLE = 'New analysis';

/**
 * TranscriptStore over the platform chat-history endpoints (docs/frontend_transcripts.md).
 *
 * The server is the system of record: POST /api/chat appends the user and assistant messages itself,
 * so `appendMessage` only tracks chats that are mid-first-turn (so the sidebar shows them at once,
 * QA-S2) and the client-generated short title. Both live in this object's memory only: never
 * localStorage, URLs or logs. The short title is shown instead of the server's generic
 * "New analysis" until the user renames the chat (a rename is sent to the server).
 *
 * Not on the server yet (contract follow-up): pins, truncate/replace for regenerate/edit branches
 * (the server records those as appended messages with parentId). Those are no-ops here and the UI
 * hides Pin because `setPinned` is absent.
 */
export class HttpTranscriptStore implements TranscriptStore {
  mode: TranscriptStore['mode'] = 'ephemeral';
  retentionDays: number | null = null;
  readonly ready: Promise<void>;
  private api: ConversationsApi;
  private titles = new Map<ID, string>();
  private pending = new Map<ID, ThreadSummary>();
  private known = new Set<ID>();
  /** Conversation the server refused (shredded key etc.); surfaced by the UI as an error later. */
  lastError: ConversationsApiError | null = null;

  constructor(base = '', token?: string, fetchImpl?: typeof fetch) {
    this.api = new ConversationsApi(base, token, fetchImpl);
    this.ready = this.api.info().then(
      (i) => { this.mode = i.persisted ? 'server-encrypted' : 'ephemeral'; this.retentionDays = i.retentionDays; },
      (e) => { this.lastError = e instanceof ConversationsApiError ? e : null; },
    );
  }

  private title(id: ID, server: string) {
    return server === SERVER_DEFAULT_TITLE ? this.titles.get(id) ?? server : server;
  }

  async listConversations(cursor?: string): Promise<{ items: ThreadSummary[]; next?: string }> {
    let page: Awaited<ReturnType<ConversationsApi['list']>> = { items: [] };
    try { page = await this.api.list(cursor); this.lastError = null; }
    catch (e) { if (!(e instanceof ConversationsApiError)) throw e; this.lastError = e; }
    const items: ThreadSummary[] = page.items.map((c) => ({ id: c.id, title: this.title(c.id, c.title), updatedAt: c.updatedAt }));
    for (const id of items.map((i) => i.id)) { this.pending.delete(id); this.known.add(id); }
    // chats the server hasn't recorded yet (first turn still connecting) go on top
    if (!cursor) items.unshift(...[...this.pending.values()].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)));
    return { items, next: page.next };
  }

  async getConversation(id: ID): Promise<{ id: ID; messages: Message[] } | undefined> {
    const c = await this.api.get(id);
    if (c) this.known.add(id);
    return c ? { id: c.id, messages: c.messages.map(hydrate) } : undefined;
  }

  async appendMessage(conversationId: ID, _msg: UserMessage | AssistantMessage, title?: string): Promise<void> {
    if (title && !this.titles.has(conversationId)) this.titles.set(conversationId, title);
    if (_msg.role === 'user' && !this.pending.has(conversationId) && !this.known.has(conversationId)) {
      this.pending.set(conversationId, { id: conversationId, title: title ?? SERVER_DEFAULT_TITLE, updatedAt: new Date().toISOString() as ISODate });
    }
  }

  async truncateAfter(): Promise<void> {}
  async replaceMessages(): Promise<void> {}

  async deleteConversation(id: ID): Promise<void> {
    this.pending.delete(id);
    this.titles.delete(id);
    this.known.delete(id);
    await this.api.remove(id);
  }

  async deleteAll(): Promise<number> {
    this.pending.clear();
    this.titles.clear();
    this.known.clear();
    return (await this.api.removeAll()).deleted;
  }

  async renameConversation(id: ID, title: string): Promise<void> {
    const t = title.trim();
    if (!t) return;
    this.titles.set(id, t);
    await this.api.rename(id, t);
  }

  async setRetention(policy: { days: number }): Promise<void> {
    this.retentionDays = (await this.api.setRetention(policy.days)).retentionDays;
  }

  /** Server-side search (POST body, never a URL). Snippet offsets are recomputed client-side for highlighting. */
  async search(query: string, limit = 30): Promise<SearchHit[]> {
    const q = query.trim();
    if (!q) return [];
    const page = await this.api.search(q);
    const lq = q.toLowerCase();
    return page.items.slice(0, limit).map((c) => {
      const title = this.title(c.id, c.title);
      const snippet = c.snippet ?? title;
      const at = snippet.toLowerCase().indexOf(lq);
      return { id: c.id, title, updatedAt: c.updatedAt, snippet, matchStart: Math.max(0, at), matchLength: at >= 0 ? q.length : 0 };
    });
  }
}
