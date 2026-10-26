import type { StreamEvent } from '../lib/contract';
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
}

const abortError = () => new DOMException('aborted', 'AbortError');

export class HttpAnalystAdapter implements AnalystAdapter {
  readonly name = 'http';
  private current: string | null = null;
  /**
   * `token` is only for the backend's demo mode (`Bearer demo:<customer>:<user>`, synthetic data).
   * Real deployments leave it unset and authenticate with the httpOnly `cc_session` cookie.
   */
  constructor(private base = '', private token?: string, opts: Partial<HttpAdapterOptions> = {}) {
    this.opts = { maxRetries: 3, retryBaseMs: 500, stopGraceMs: 1500, ...opts };
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

  async *streamTurn({ conversationId, messages, signal, resume }: Parameters<AnalystAdapter['streamTurn']>[0]): AsyncIterable<AnyStreamEvent> {
    // The server takes user turns only; prior assistant prose is reconstructed server-side.
    const users = messages.filter((m) => m.role === 'user').map(({ id, role, text }) => ({ id, role, text }));
    let body = JSON.stringify({ conversationId, messages: users, ...(resume ? { resume } : {}) });
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
          if (messageId !== null && retries < this.opts.maxRetries) { await this.backoff(++retries, signal); continue; }
          throw e;
        }
        // §6.4: an expired/answered clarification is 409; send the reply as a fresh question instead.
        if (res.status === 409 && resumeFallback && messageId === null) {
          resumeFallback = false;
          body = JSON.stringify({ conversationId, messages: users });
          continue;
        }
        if (!res.ok || !res.body) {
          if (messageId !== null && res.status >= 500 && retries < this.opts.maxRetries) { await this.backoff(++retries, signal); continue; }
          if (messageId !== null) throw new Error(`replay ${res.status}`); // the run is gone: useChat shows "Connection lost"
          yield { type: 'part', part: { type: 'error', id: 'err-http', code: 'internal', title: 'Connection lost', message: `The analyst returned ${res.status}.`, retryable: true } };
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
            yield ev;
            if (ev.type === 'message.end') return;
          }
        } catch (e) {
          if (wire.signal.aborted) throw abortError();
          if (messageId === null) throw e;
        }
        // The stream closed without message.end: a dropped connection.
        if (signal.aborted) throw abortError();
        if (messageId === null || retries >= this.opts.maxRetries) throw new Error('stream dropped');
        await this.backoff(++retries, signal);
      }
    } finally {
      clearTimeout(graceTimer);
      signal.removeEventListener('abort', onAbort);
      this.current = null;
    }
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
