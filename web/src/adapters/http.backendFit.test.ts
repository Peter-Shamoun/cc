import { afterEach, describe, expect, it, vi } from 'vitest';
import { HttpAnalystAdapter } from './http';
import { demoTokenSession } from '../auth/session';

/**
 * BACKEND-FIT (Peter, 11:57 PM): the user never picks a database, lake, schema or view. The server derives the
 * customer and the relations from the session (Cognito token -> ccsim.query.open_session), so the wire must not
 * carry any data selection: no query string, no data-ish headers, and a body of only the turn (no options at all:
 * there is no strategy/model choice since contract 1.24.0). api/tests/test_backend_fit.py proves the server ignores such hints anyway.
 */
const BODY_KEYS = new Set(['conversationId', 'messages', 'resume', 'regenerate', 'temporary']);
const HEADER_KEYS = new Set(['authorization', 'content-type', 'accept', 'last-event-id', 'x-cc-csrf', 'idempotency-key']);
const DATA_WORDS = /customer|tenant|org|schema|database|lake|scope|view|relation|source|escalat|lossless/i;

const enc = new TextEncoder();
const sse = (evs: object[]) => new Response(new ReadableStream({ start(c) { for (const e of evs) c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); c.close(); } }), { status: 200, headers: { 'content-type': 'text/event-stream' } });
const drain = async (it: AsyncIterable<unknown>) => { for await (const _ of it) { /* consume */ } };

afterEach(() => vi.unstubAllGlobals());

describe('the chat request carries no data selection (BACKEND-FIT)', () => {
  it('only the turn goes on the wire; identity is the session header', async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, init });
      return sse([{ type: 'message.start', messageId: 'm1', conversationId: 'c1', contractVersion: '1.20.0', seq: 0 },
        { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 1 }]);
    }));
    const a = new HttpAnalystAdapter('', demoTokenSession('demo:acme_labs:qa'));
    await drain(a.streamTurn({ conversationId: 'c1', messages: [{ id: 'u1', role: 'user', text: 'Denials by payer' }], signal: new AbortController().signal }));
    await drain(a.streamTurn({ conversationId: 'c1', messages: [{ id: 'u1', role: 'user', text: 'x' }], signal: new AbortController().signal, regenerate: { messageId: 'm1', style: 'concise' } }));
    expect(calls.length).toBeGreaterThanOrEqual(2);
    for (const { url, init } of calls) {
      expect(url).not.toContain('?');
      const headers = Object.keys(Object.fromEntries(new Headers(init.headers).entries()));
      expect(headers.filter((h) => !HEADER_KEYS.has(h))).toEqual([]);
      if (!init.body) continue;
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      expect(Object.keys(body).filter((k) => !BODY_KEYS.has(k))).toEqual([]);
      expect(JSON.stringify(Object.keys(body))).not.toMatch(DATA_WORDS);
    }
  });
});
