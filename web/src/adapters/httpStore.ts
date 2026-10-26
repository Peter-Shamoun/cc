import type { AssistantMessage, ID, ISODate, Message, UserMessage } from '../lib/contract';
import type { AuthSession } from '../auth/session';
import type { SearchHit, ThreadSummary, TranscriptStore } from './types';
import { ConversationsApi, ConversationsApiError } from './conversationsApi';
import { shortTitle } from '../shell/title';
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
/** After a reload, how many untitled chats get a client title per list call, how many fetches at once, and how long the list waits. */
const TITLE_FETCH = { limit: 12, concurrency: 4, waitMs: 1500 };

/**
 * TranscriptStore over the platform chat-history endpoints (docs/frontend_transcripts.md).
 *
 * The server is the system of record: POST /api/chat appends the user and assistant messages itself,
 * so `appendMessage` only tracks chats that are mid-first-turn (so the sidebar shows them at once,
 * QA-S2) and the client-generated short title. Both live in this object's memory only: never
 * localStorage, URLs or logs. The short title is shown instead of the server's generic
 * "New analysis" until the user renames the chat (a rename is sent to the server).
 *
 * Pin/archive (contract 1.17.0) are server-side once `enablePinArchive(true)` is called for a server whose
 * Capabilities.pinArchive is true; until then `setPinned`/`setArchived` are absent and the UI hides them.
 * Truncate/replace for regenerate/edit branches stay no-ops (the server records those as appended
 * messages with parentId).
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
  /** Present only when the server persists pins/archives (1.17.0, Capabilities.pinArchive). */
  setPinned?: (id: ID, pinned: boolean) => Promise<void>;
  setArchived?: (id: ID, archived: boolean) => Promise<void>;
  private pinArchive = false;

  /** Turn server-side pin/archive on or off (from Capabilities.pinArchive). */
  enablePinArchive(on: boolean) {
    this.pinArchive = on;
    if (on) {
      this.setPinned = async (id, pinned) => { await this.api.setPinned(id, pinned); };
      this.setArchived = async (id, archived) => { await this.api.setArchived(id, archived); };
    } else {
      delete this.setPinned;
      delete this.setArchived;
    }
  }

  constructor(base = '', auth?: string | AuthSession, fetchImpl?: typeof fetch) {
    this.api = new ConversationsApi(base, auth, fetchImpl);
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
    try {
      if (this.pinArchive) {
        // 1.17.0: the sidebar page excludes archived chats (pins first, server order); Settings → Archived chats
        // reads the archived ones from the same thread list, so the first page also fetches `only`.
        const [main, archived] = await Promise.all([this.api.list(cursor, 'exclude'), cursor ? undefined : this.api.list(undefined, 'only')]);
        page = { items: [...main.items, ...(archived?.items ?? [])], next: main.next };
      } else page = await this.api.list(cursor);
      this.lastError = null;
    } catch (e) { if (!(e instanceof ConversationsApiError)) throw e; this.lastError = e; }
    await this.deriveTitles(page.items.filter((c) => c.title === SERVER_DEFAULT_TITLE).map((c) => c.id));
    const items: ThreadSummary[] = page.items.map((c) => ({
      id: c.id, title: this.title(c.id, c.title), updatedAt: c.updatedAt,
      ...(this.pinArchive ? { pinned: !!c.pinned, archived: !!c.archived } : {}),
    }));
    for (const id of items.map((i) => i.id)) { this.pending.delete(id); this.known.add(id); }
    // chats the server hasn't recorded yet (first turn still connecting) go on top
    if (!cursor) items.unshift(...[...this.pending.values()].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)));
    return { items, next: page.next };
  }

  /**
   * The server keeps chats titled "New analysis" (it never echoes a question as a title,
   * docs/frontend_transcripts.md). After a reload this tab has lost the short titles it made, so
   * rebuild them the same way, in memory only: shortTitle(first question). Bounded and best-effort;
   * chats not done within waitMs get their title on the next list.
   */
  private deriving = new Map<ID, Promise<void>>();
  private deriveTitles(ids: ID[]): Promise<void> {
    const todo = ids.filter((id) => !this.titles.has(id)).slice(0, TITLE_FETCH.limit);
    if (!todo.length) return Promise.resolve();
    const one = (id: ID) => {
      let p = this.deriving.get(id);
      if (!p) {
        p = this.api.get(id).then((c) => { if (c) this.learnTitle(id, c.messages); }, () => undefined).finally(() => this.deriving.delete(id));
        this.deriving.set(id, p);
      }
      return p;
    };
    const queue = [...todo];
    const worker = async () => { for (let id = queue.shift(); id; id = queue.shift()) await one(id); };
    const all = Promise.all(Array.from({ length: Math.min(TITLE_FETCH.concurrency, todo.length) }, worker)).then(() => undefined);
    return Promise.race([all, new Promise<void>((r) => setTimeout(r, TITLE_FETCH.waitMs))]);
  }

  private learnTitle(id: ID, messages: Message[]) {
    const first = messages.find((m) => m.role === 'user') as UserMessage | undefined;
    if (first?.text && !this.titles.has(id)) this.titles.set(id, shortTitle(first.text));
  }

  async getConversation(id: ID): Promise<{ id: ID; messages: Message[] } | undefined> {
    const c = await this.api.get(id);
    if (c) this.known.add(id);
    if (c) this.learnTitle(id, c.messages);
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
