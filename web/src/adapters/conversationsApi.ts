import type { ID, ISODate, Message } from '../lib/contract';
import { asSession, type AuthSession } from '../auth/session';

/**
 * Client for the server chat-history endpoints (api/app.py, docs/frontend_transcripts.md).
 * The server is the only transcript store; this client keeps nothing in browser storage.
 * Search is a POST so the search text (possibly PHI) never appears in a URL.
 */
export type TranscriptMode = 'ephemeral' | 'persisted';
/** 1.17.0: `pinned` / `archived` (always sent by 1.17+ servers; see Capabilities.pinArchive). */
export interface ConversationSummary { id: ID; title: string; updatedAt: ISODate; messageCount: number; snippet?: string; pinned?: boolean; archived?: boolean }
/** 1.17.0: GET /api/conversations?archived= and search: include (default) / exclude (sidebar) / only (Archived chats). */
export type ArchivedFilter = 'include' | 'exclude' | 'only';
/** PATCH /api/conversations/{id} response. */
export interface ConversationUpdate { id: ID; title: string; updatedAt: ISODate; pinned?: boolean; archived?: boolean }
export interface ConversationPage { items: ConversationSummary[]; next?: string }
export interface ConversationDetail { id: ID; title: string; updatedAt: ISODate; messages: Message[] }
export interface TranscriptInfo { mode: TranscriptMode; persisted: boolean; retentionDays: number | null }

export class ConversationsApiError extends Error {
  constructor(readonly status: number, readonly code: string) { super(`conversations ${status} ${code}`); }
}

export class ConversationsApi {
  private readonly auth: AuthSession;
  constructor(private base = '', auth?: string | AuthSession, private fetchImpl: typeof fetch = (...a) => fetch(...a)) {
    this.auth = asSession(auth);
  }

  private async call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const headers: Record<string, string> = { accept: 'application/json' };
    if (body !== undefined) headers['content-type'] = 'application/json';
    Object.assign(headers, this.auth.headers());
    const res = await this.fetchImpl(`${this.base}${path}`, {
      method, headers, credentials: 'same-origin', cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      if (res.status === 401) this.auth.onExpired();
      const code = await res.json().then((j: { error?: { code?: string } }) => j?.error?.code ?? 'error', () => 'error');
      throw new ConversationsApiError(res.status, code);
    }
    return res.json() as Promise<T>;
  }

  info() { return this.call<TranscriptInfo>('GET', '/api/transcripts'); }
  list(cursor?: string, archived?: ArchivedFilter) {
    const qs = new URLSearchParams();
    if (cursor) qs.set('cursor', cursor);
    if (archived) qs.set('archived', archived);
    const q = qs.toString();
    return this.call<ConversationPage>('GET', `/api/conversations${q ? `?${q}` : ''}`);
  }
  search(q: string, cursor?: string, archived?: ArchivedFilter) {
    return this.call<ConversationPage>('POST', '/api/conversations/search', archived ? { q, cursor, archived } : { q, cursor });
  }
  async get(id: ID): Promise<ConversationDetail | undefined> {
    try { return await this.call<ConversationDetail>('GET', `/api/conversations/${encodeURIComponent(id)}`); }
    catch (e) { if (e instanceof ConversationsApiError && e.status === 404) return undefined; throw e; }
  }
  rename(id: ID, title: string) { return this.update(id, { title }); }
  /** 1.17.0: pin / archive persist server-side (archiving drops the pin; pinning unarchives). */
  setPinned(id: ID, pinned: boolean) { return this.update(id, { pinned }); }
  setArchived(id: ID, archived: boolean) { return this.update(id, { archived }); }
  update(id: ID, patch: { title?: string; pinned?: boolean; archived?: boolean }) {
    return this.call<ConversationUpdate>('PATCH', `/api/conversations/${encodeURIComponent(id)}`, patch);
  }
  async remove(id: ID): Promise<boolean> {
    try { return (await this.call<{ deleted: boolean }>('DELETE', `/api/conversations/${encodeURIComponent(id)}`)).deleted; }
    catch (e) { if (e instanceof ConversationsApiError && e.status === 404) return false; throw e; }
  }
  removeAll() { return this.call<{ deleted: number }>('DELETE', '/api/conversations'); }
  setRetention(days: number) { return this.call<{ mode: TranscriptMode; retentionDays: number }>('PUT', '/api/transcripts/retention', { days }); }
}
