import type { ID, ISODate, Message } from '../lib/contract';
import { asSession, type AuthSession } from '../auth/session';
import { fetchRetrying, readJsonCapped, ResponseTooLargeError } from '../lib/fetchGuard';

/** Body caps: a page of summaries is small; one conversation can hold large tables. */
const LIST_MAX_BYTES = 8 * 1024 * 1024;
const DETAIL_MAX_BYTES = 32 * 1024 * 1024;

/**
 * Client for the server chat-history endpoints (api/app.py, docs/frontend_transcripts.md).
 * The server is the only transcript store; this client keeps nothing in browser storage.
 * Search is a POST so the search text (possibly PHI) never appears in a URL.
 */
export type TranscriptMode = 'ephemeral' | 'persisted';
export interface ConversationSummary { id: ID; title: string; updatedAt: ISODate; messageCount: number; snippet?: string }
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

  private async call<T>(method: string, path: string, body?: unknown, maxBytes = LIST_MAX_BYTES): Promise<T> {
    const headers: Record<string, string> = { accept: 'application/json' };
    if (body !== undefined) headers['content-type'] = 'application/json';
    Object.assign(headers, this.auth.headers());
    // 429: the server did nothing, so waiting out Retry-After and sending the same request again is safe.
    const res = await fetchRetrying(this.fetchImpl, `${this.base}${path}`, {
      method, headers, credentials: 'same-origin', cache: 'no-store',
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      if (res.status === 401) this.auth.onExpired();
      const code = await res.json().then((j: { error?: { code?: string } }) => j?.error?.code ?? 'error', () => 'error');
      throw new ConversationsApiError(res.status, code);
    }
    try { return await readJsonCapped<T>(res, maxBytes); }
    catch (e) { if (e instanceof ResponseTooLargeError) throw new ConversationsApiError(res.status, 'response_too_large'); throw e; }
  }

  info() { return this.call<TranscriptInfo>('GET', '/api/transcripts'); }
  list(cursor?: string) {
    return this.call<ConversationPage>('GET', `/api/conversations${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ''}`);
  }
  search(q: string, cursor?: string) { return this.call<ConversationPage>('POST', '/api/conversations/search', { q, cursor }); }
  async get(id: ID): Promise<ConversationDetail | undefined> {
    try { return await this.call<ConversationDetail>('GET', `/api/conversations/${encodeURIComponent(id)}`, undefined, DETAIL_MAX_BYTES); }
    catch (e) { if (e instanceof ConversationsApiError && e.status === 404) return undefined; throw e; }
  }
  rename(id: ID, title: string) { return this.call<{ id: ID; title: string; updatedAt: ISODate }>('PATCH', `/api/conversations/${encodeURIComponent(id)}`, { title }); }
  async remove(id: ID): Promise<boolean> {
    try { return (await this.call<{ deleted: boolean }>('DELETE', `/api/conversations/${encodeURIComponent(id)}`)).deleted; }
    catch (e) { if (e instanceof ConversationsApiError && e.status === 404) return false; throw e; }
  }
  removeAll() { return this.call<{ deleted: number }>('DELETE', '/api/conversations'); }
  setRetention(days: number) { return this.call<{ mode: TranscriptMode; retentionDays: number }>('PUT', '/api/transcripts/retention', { days }); }
}
