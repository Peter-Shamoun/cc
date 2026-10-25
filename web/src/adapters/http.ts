import type { StreamEvent } from '../lib/contract';
import type { AnalystAdapter } from './types';

/**
 * Adapter for the real analyst endpoint (spec §5). POST {base}/api/chat with
 * {conversationId, messages}; response is text/event-stream with one JSON
 * StreamEvent per `data:` line. Auth is an httpOnly cookie (credentials:
 * 'same-origin'); nothing PHI-bearing goes in a URL.
 */
export class HttpAnalystAdapter implements AnalystAdapter {
  readonly name = 'http';
  constructor(private base = '') {}

  async *streamTurn({ conversationId, messages, signal }: Parameters<AnalystAdapter['streamTurn']>[0]): AsyncIterable<StreamEvent> {
    const res = await fetch(`${this.base}/api/chat`, {
      method: 'POST',
      credentials: 'same-origin',
      cache: 'no-store',
      headers: { 'content-type': 'application/json', accept: 'text/event-stream' },
      body: JSON.stringify({ conversationId, messages }),
      signal,
    });
    if (!res.ok || !res.body) {
      yield { type: 'part', part: { type: 'error', id: 'err-http', code: 'internal', title: 'Connection lost', message: `The analyst returned ${res.status}.`, retryable: true } };
      yield { type: 'message.end', messageId: '', finishReason: 'error' };
      return;
    }
    yield* parseSse(res.body);
  }

  async fetchBlob(id: string): Promise<Blob> {
    const res = await fetch(`${this.base}/api/blobs/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store' });
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
