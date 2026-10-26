import type { ErrorPart, StreamEvent } from '../lib/contract';
import type { HttpError } from '../api/contract';
import type { AnalystAdapter, AnyStreamEvent } from './types';

/**
 * Adapter for the real analyst endpoint (spec §5). POST {base}/api/chat with
 * {conversationId, messages}; response is text/event-stream with one JSON
 * StreamEvent per `data:` line. Auth is an httpOnly cookie (credentials:
 * 'same-origin'); nothing PHI-bearing goes in a URL.
 */
export interface HttpAdapterOptions {
  /** Reconnect attempts after a dropped stream (Last-Event-ID replay). */
  maxRetries: number;
  retryBaseMs: number;
  /** After Stop, how long to keep reading for the server's aborted message.end (with usage). */
  stopGraceMs: number;
  /** How long a dropped stream waits for the browser to come back online before giving up. */
  offlineWaitMs: number;
}

const abortError = () => new DOMException('aborted', 'AbortError');
const isOffline = () => typeof navigator !== 'undefined' && navigator.onLine === false;

/** The request never reached the server (offline, DNS, CORS, server down). */
export function networkError(): ErrorPart {
  return isOffline()
    ? { type: 'error', id: 'err-offline', code: 'offline', title: "You're offline", message: 'Check your connection, then retry.', retryable: true }
    : { type: 'error', id: 'err-network', code: 'internal', title: "Can't reach the analyst", message: 'The server did not respond. Try again in a moment.', retryable: true };
}

/**
 * Map a non-stream HTTP failure (CONTRACT.md §2: `{"error": {code, message}}`) to an error part.
 * Server messages are fixed, PHI-free strings; we still fall back to our own copy per code.
 */
export async function httpError(res: Response): Promise<ErrorPart> {
  let code = '';
  let message = '';
  try {
    const j = (await res.json()) as Partial<HttpError>;
    code = j.error?.code ?? '';
    message = j.error?.message ?? '';
  } catch { /* not JSON */ }
  const retryAfter = Number(res.headers.get('retry-after'));
  const base = { type: 'error' as const, id: `err-http-${res.status}` };
  if (res.status === 401 || code === 'auth_expired') return { ...base, code: 'auth_expired', title: 'Session expired', message: message || 'Sign in again to continue.', retryable: false };
  if (res.status === 429 || code === 'rate_limited') return { ...base, code: 'rate_limited', title: 'Too many requests', message: message || 'Wait a moment, then retry.', retryable: true, ...(retryAfter > 0 ? { retryAfterMs: retryAfter * 1000 } : {}) };
  if (res.status === 403 || res.status === 404 || code === 'access_denied') return { ...base, code: 'access_denied', title: 'Not available', message: message || "You don't have access to this conversation.", retryable: false };
  if (res.status === 410) return { ...base, code: code || 'transcripts_unavailable', title: 'Chat unavailable', message: message || 'This chat is no longer available.', retryable: false };
  if (res.status === 422 || code === 'validation') return { ...base, code: 'validation', title: "Couldn't send that", message: message || 'Check the question and try again.', retryable: false };
  return { ...base, code: code || 'internal', title: 'Analyst unavailable', message: `The analyst returned ${res.status}. Try again in a moment.`, retryable: true };
}

export class HttpAnalystAdapter implements AnalystAdapter {
  readonly name = 'http';
  private current: string | null = null;
  /**
   * `token` is only for the backend's demo mode (`Bearer demo:<customer>:<user>`, synthetic data).
   * Real deployments leave it unset and authenticate with the httpOnly `cc_session` cookie.
   */
  constructor(private base = '', private token?: string, opts: Partial<HttpAdapterOptions> = {}) {
    this.opts = { maxRetries: 3, retryBaseMs: 500, stopGraceMs: 1500, offlineWaitMs: 45_000, ...opts };
  }
  private readonly opts: HttpAdapterOptions;

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return this.token ? { ...extra, authorization: `Bearer ${this.token}` } : extra;
  }

  /** Server-side cancel (POST /api/chat/{messageId}/stop); the fetch abort alone only drops the socket. */
  async stop(): Promise<void> {
    if (!this.current) return;
    await fetch(`${this.base}/api/chat/${encodeURIComponent(this.current)}/stop`, { method: 'POST', credentials: 'same-origin', headers: this.headers() }).catch(() => undefined);
  }

  async *streamTurn({ conversationId, messages, signal, resume, regenerate, onConnection }: Parameters<AnalystAdapter['streamTurn']>[0]): AsyncIterable<AnyStreamEvent> {
    // The server takes user turns only; prior assistant prose is reconstructed server-side.
    const users = messages.filter((m) => m.role === 'user').map(({ id, role, text, parentId }) => ({ id, role, text, ...(parentId ? { parentId } : {}) }));
    let body = JSON.stringify({ conversationId, messages: users, ...(resume ? { resume } : {}), ...(regenerate ? { regenerate } : {}) });
    let regenerateFallback = !!regenerate;
    // `signal` is the user's Stop. We keep reading after it fires so the server's
    // `message.end {finishReason: 'aborted', usage}` still arrives (CONTRACT.md §8: within 1 s);
    // the socket itself is only dropped after STOP_GRACE_MS.
    const wire = new AbortController();
    let graceTimer: ReturnType<typeof setTimeout> | undefined;
    const onAbort = () => {
      void this.stop();
      graceTimer = setTimeout(() => wire.abort(), this.opts.stopGraceMs);
      if (!this.current) wire.abort(); // nothing started server-side yet: nothing to wait for
    };
    if (signal.aborted) throw new DOMException('aborted', 'AbortError');
    signal.addEventListener('abort', onAbort, { once: true });
    let messageId: string | null = null;
    let lastSeq = -1;
    let retries = 0;
    let dropped = false; // between a drop and the first replayed event
    let resumeFallback = !!resume;
    try {
      for (;;) {
        // Reconnect (CONTRACT.md §8): same POST, plus Last-Event-ID so the server replays only what we missed.
        const replay: Record<string, string> = messageId !== null ? { 'last-event-id': `${messageId}:${lastSeq}` } : {};
        let res: Response;
        try {
          res = await fetch(`${this.base}/api/chat`, {
            method: 'POST',
            credentials: 'same-origin',
            cache: 'no-store',
            headers: this.headers({ 'content-type': 'application/json', accept: 'text/event-stream', ...replay }),
            body,
            signal: wire.signal,
          });
        } catch (e) {
          if (wire.signal.aborted || signal.aborted) throw abortError();
          if (messageId !== null && isOffline()) { onConnection?.('offline'); dropped = true; await this.waitOnline(signal); continue; } // offline: wait, don't burn retries
          if (messageId !== null && retries < this.opts.maxRetries) { onConnection?.('reconnecting'); dropped = true; await this.backoff(++retries, signal); continue; }
          if (messageId !== null) throw e;
          // Never reached the server: say why, as a retryable error part (Retry = regenerate).
          yield { type: 'part', part: networkError() };
          yield { type: 'message.end', messageId: '', finishReason: 'error' };
          return;
        }
        // §6.4: an expired/answered clarification is 409; send the reply as a fresh question instead.
        if (res.status === 409 && resumeFallback && messageId === null) {
          resumeFallback = false;
          body = JSON.stringify({ conversationId, messages: users });
          continue;
        }
        // The server no longer has the answer to regenerate (ephemeral history, other branch): ask again.
        if (res.status === 404 && regenerateFallback && messageId === null) {
          regenerateFallback = false;
          body = JSON.stringify({ conversationId, messages: users });
          continue;
        }
        if (!res.ok || !res.body) {
          if (messageId !== null && res.status >= 500 && retries < this.opts.maxRetries) { onConnection?.('reconnecting'); dropped = true; await this.backoff(++retries, signal); continue; }
          if (messageId !== null) throw new Error(`replay ${res.status}`); // the run is gone: useChat shows "Connection lost"
          yield { type: 'part', part: await httpError(res) };
          yield { type: 'message.end', messageId: '', finishReason: 'error' };
          return;
        }
        try {
          for await (const ev of parseSse(res.body)) {
            if ((ev as { type: string }).type === 'heartbeat') continue;
            const seq = (ev as { seq?: number }).seq;
            if (typeof seq === 'number') {
              if (seq <= lastSeq) continue; // replay overlap
              lastSeq = seq;
            }
            if (ev.type === 'message.start') this.current = messageId = ev.messageId;
            retries = 0;
            if (dropped) { dropped = false; onConnection?.('live'); }
            yield ev;
            if (ev.type === 'message.end') return;
          }
        } catch (e) {
          if (wire.signal.aborted) throw abortError();
          if (messageId === null) throw e;
        }
        // The stream closed without message.end: a dropped connection.
        if (signal.aborted) throw abortError();
        if (isOffline()) { onConnection?.('offline'); dropped = true; await this.waitOnline(signal); continue; }
        if (messageId === null || retries >= this.opts.maxRetries) throw new Error('stream dropped');
        onConnection?.('reconnecting');
        dropped = true;
        await this.backoff(++retries, signal);
      }
    } finally {
      clearTimeout(graceTimer);
      signal.removeEventListener('abort', onAbort);
      this.current = null;
    }
  }

  /** Wait for the browser to come back online (bounded: the server replays for 60 s after a run ends). */
  private waitOnline(signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      const done = () => { clearTimeout(t); window.removeEventListener('online', done); signal.removeEventListener('abort', aborted); resolve(); };
      const aborted = () => { clearTimeout(t); window.removeEventListener('online', done); reject(abortError()); };
      const t = setTimeout(() => { window.removeEventListener('online', done); signal.removeEventListener('abort', aborted); reject(new Error('offline')); }, this.opts.offlineWaitMs);
      if (signal.aborted) return aborted();
      window.addEventListener('online', done, { once: true });
      signal.addEventListener('abort', aborted, { once: true });
    });
  }

  private backoff(attempt: number, signal: AbortSignal): Promise<void> {
    const ms = Math.min(this.opts.retryBaseMs * 2 ** (attempt - 1), 4000);
    return new Promise((resolve, reject) => {
      if (signal.aborted) return reject(abortError());
      const t = setTimeout(resolve, ms);
      signal.addEventListener('abort', () => { clearTimeout(t); reject(abortError()); }, { once: true });
    });
  }

  async fetchBlob(id: string): Promise<Blob> {
    const res = await fetch(`${this.base}/api/download/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store', headers: this.headers() });
    if (!res.ok) throw new Error(`blob ${res.status}`);
    return res.blob();
  }
}

/** Minimal SSE parser: yields the JSON of each `data:` event. */
export async function* parseSse(body: ReadableStream<Uint8Array>): AsyncIterable<StreamEvent> {
  const reader = body.getReader();
  const dec = new TextDecoder();
  let buf = '';
  const flush = function* (chunk: string) {
    const data = chunk
      .split('\n')
      .filter((l) => l.startsWith('data:'))
      .map((l) => l.slice(5).trimStart())
      .join('\n');
    if (data) yield JSON.parse(data) as StreamEvent;
  };
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true }).replace(/\r\n/g, '\n');
    let sep: number;
    while ((sep = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, sep);
      buf = buf.slice(sep + 2);
      yield* flush(chunk);
    }
  }
  if (buf.trim()) yield* flush(buf);
}
