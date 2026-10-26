import type { StreamEvent } from '../lib/contract';
import type { AnalystAdapter, AnyStreamEvent } from './types';

/**
 * Adapter for the real analyst endpoint (spec §5). POST {base}/api/chat with
 * {conversationId, messages}; response is text/event-stream with one JSON
 * StreamEvent per `data:` line. Auth is an httpOnly cookie (credentials:
 * 'same-origin'); nothing PHI-bearing goes in a URL.
 */
export class HttpAnalystAdapter implements AnalystAdapter {
  readonly name = 'http';
  private current: string | null = null;
  /**
   * `token` is only for the backend's demo mode (`Bearer demo:<customer>:<user>`, synthetic data).
   * Real deployments leave it unset and authenticate with the httpOnly `cc_session` cookie.
   */
  constructor(private base = '', private token?: string) {}

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return this.token ? { ...extra, authorization: `Bearer ${this.token}` } : extra;
  }

  /** Server-side cancel (POST /api/chat/{messageId}/stop); the fetch abort alone only drops the socket. */
  async stop(): Promise<void> {
    if (!this.current) return;
    await fetch(`${this.base}/api/chat/${encodeURIComponent(this.current)}/stop`, { method: 'POST', credentials: 'same-origin', headers: this.headers() }).catch(() => undefined);
  }

  async *streamTurn({ conversationId, messages, signal, resume }: Parameters<AnalystAdapter['streamTurn']>[0]): AsyncIterable<AnyStreamEvent> {
    const res = await fetch(`${this.base}/api/chat`, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: this.headers({ 'content-type': 'application/json', accept: 'text/event-stream' }),
      // The server takes user turns only; prior assistant prose is reconstructed server-side.
      body: JSON.stringify({ conversationId, messages: messages.filter((m) => m.role === 'user').map(({ id, role, text }) => ({ id, role, text })), ...(resume ? { resume } : {}) }),
      signal,
    });
    if (!res.ok || !res.body) {
      yield { type: 'part', part: { type: 'error', id: 'err-http', code: 'internal', title: 'Connection lost', message: `The analyst returned ${res.status}.`, retryable: true } };
      yield { type: 'message.end', messageId: '', finishReason: 'error' };
      return;
    }
    const onAbort = () => void this.stop();
    signal.addEventListener('abort', onAbort, { once: true });
    try {
      for await (const ev of parseSse(res.body)) {
        if ((ev as { type: string }).type === 'heartbeat') continue;
        if (ev.type === 'message.start') this.current = ev.messageId;
        yield ev;
      }
    } finally {
      signal.removeEventListener('abort', onAbort);
      this.current = null;
    }
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
