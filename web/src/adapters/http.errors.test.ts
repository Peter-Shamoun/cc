import { afterEach, describe, expect, it, vi } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { HttpAnalystAdapter, httpError, networkError } from './http';
import { useOnline } from '../lib/useOnline';

const turn = (signal = new AbortController().signal) => ({ conversationId: 'c', messages: [{ id: 'u', role: 'user' as const, text: 'q' }], signal });
const drain = async (it: AsyncIterable<unknown>) => { const out: unknown[] = []; for await (const e of it) out.push(e); return out; };
const setOnline = (v: boolean) => { Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => v }); window.dispatchEvent(new Event(v ? 'online' : 'offline')); };

afterEach(() => { vi.unstubAllGlobals(); setOnline(true); });

describe('http error states', () => {
  it('maps HttpError statuses to clear, correctly-retryable parts', async () => {
    const j = (status: number, code: string, headers: Record<string, string> = {}) => new Response(JSON.stringify({ error: { code, message: '' } }), { status, headers });
    expect(await httpError(j(401, 'auth_expired'))).toMatchObject({ code: 'auth_expired', title: 'Session expired', retryable: false });
    expect(await httpError(j(429, 'rate_limited', { 'retry-after': '3' }))).toMatchObject({ code: 'rate_limited', retryable: true, retryAfterMs: 3000 });
    expect(await httpError(j(404, 'access_denied'))).toMatchObject({ code: 'access_denied', retryable: false });
    expect(await httpError(j(422, 'validation'))).toMatchObject({ code: 'validation', retryable: false });
    expect(await httpError(new Response('<html>bad gateway</html>', { status: 502 }))).toMatchObject({ code: 'internal', title: 'Analyst unavailable', retryable: true });
  });

  it('a request that never reaches the server ends with a retryable error part, offline-aware', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const out = (await drain(new HttpAnalystAdapter('').streamTurn(turn()))) as { type: string; part?: { title: string }; finishReason?: string }[];
    expect(out[0].part?.title).toBe("Can't reach the analyst");
    expect(out[1]).toMatchObject({ type: 'message.end', finishReason: 'error' });
    setOnline(false);
    expect(networkError()).toMatchObject({ code: 'offline', title: "You're offline", retryable: true });
  });

  it('a stream dropped while offline waits for the network instead of burning retries', async () => {
    const enc = new TextEncoder();
    const sse = (evs: object[]) => new Response(new ReadableStream({ start(c) { for (const e of evs) c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); c.close(); } }), { status: 200 });
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async () => {
      n++;
      if (n === 1) { setOnline(false); return sse([{ type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.0.0', seq: 0 }]); }
      return sse([{ type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 1 }]);
    }));
    const p = drain(new HttpAnalystAdapter('', undefined, { maxRetries: 0, retryBaseMs: 1 }).streamTurn(turn()));
    await new Promise((r) => setTimeout(r, 30));
    expect(n).toBe(1); // parked until online
    setOnline(true);
    const out = (await p) as { type: string }[];
    expect(n).toBe(2);
    expect(out.at(-1)!.type).toBe('message.end');
  });

  it('useOnline follows the browser', () => {
    const { result } = renderHook(() => useOnline());
    expect(result.current).toBe(true);
    act(() => setOnline(false));
    expect(result.current).toBe(false);
    act(() => setOnline(true));
    expect(result.current).toBe(true);
  });
});
