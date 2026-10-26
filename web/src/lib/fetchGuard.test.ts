/** Non-stream endpoints: 429 + Retry-After is waited out, and oversized bodies are refused before parsing. */
import { describe, expect, it, vi } from 'vitest';
import { fetchRetrying, readJsonCapped, ResponseTooLargeError } from './fetchGuard';
import { ConversationsApi, ConversationsApiError } from '../adapters/conversationsApi';
import { loadCapabilities, DEMO_CAPABILITIES } from '../contract/capabilities';

const r429 = (h: Record<string, string> = {}) => new Response(JSON.stringify({ error: { code: 'rate_limited', message: '' } }), { status: 429, headers: h });
const ok = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
const noSleep = vi.fn(async () => {});

describe('fetchRetrying', () => {
  it('waits out Retry-After (seconds or date vs the response Date) and sends the same request again', async () => {
    const seq = [r429({ 'retry-after': '2' }), r429({ 'retry-after': 'Sun, 25 Oct 2026 20:00:03 GMT', date: 'Sun, 25 Oct 2026 20:00:00 GMT' }), ok({ a: 1 })];
    const f = vi.fn(async () => seq.shift()!);
    const sleep = vi.fn(async (_ms: number) => {});
    const res = await fetchRetrying(f as never, '/api/x', { method: 'POST', body: '{}' }, { sleep });
    expect(res.status).toBe(200);
    expect(sleep.mock.calls.map((c) => c[0])).toEqual([2000, 3000]);
    expect(f.mock.calls.every((c) => (c as unknown[])[1] && ((c as unknown[])[1] as RequestInit).body === '{}')).toBe(true);
  });

  it('gives the 429 back past maxRetries or when the wait is over the cap', async () => {
    const f = vi.fn(async () => r429({ 'retry-after': '1' }));
    expect((await fetchRetrying(f as never, '/x', {}, { sleep: noSleep, maxRetries: 2 })).status).toBe(429);
    expect(f).toHaveBeenCalledTimes(3);
    const g = vi.fn(async () => r429({ 'retry-after': '600' }));
    expect((await fetchRetrying(g as never, '/x', {}, { sleep: noSleep })).status).toBe(429);
    expect(g).toHaveBeenCalledTimes(1);
  });

  it('backs off without Retry-After, and an abort during the wait rejects', async () => {
    const ac = new AbortController();
    const f = vi.fn(async () => r429());
    const p = fetchRetrying(f as never, '/x', { signal: ac.signal });
    ac.abort();
    await expect(p).rejects.toMatchObject({ name: 'AbortError' });
  });
});

describe('readJsonCapped', () => {
  it('refuses by Content-Length without reading', async () => {
    const res = new Response('{}', { headers: { 'content-length': String(64 * 1024 * 1024) } });
    await expect(readJsonCapped(res, 1024)).rejects.toBeInstanceOf(ResponseTooLargeError);
  });
  it('refuses a body that grows past the cap while streaming (no or lying Content-Length)', async () => {
    const enc = new TextEncoder();
    let cancelled = false;
    const body = new ReadableStream<Uint8Array>({
      pull(c) { c.enqueue(enc.encode('x'.repeat(600))); },
      cancel() { cancelled = true; },
    });
    await expect(readJsonCapped(new Response(body), 1024)).rejects.toBeInstanceOf(ResponseTooLargeError);
    expect(cancelled).toBe(true);
  });
  it('parses a body under the cap, multi-byte text included', async () => {
    expect(await readJsonCapped<{ t: string }>(ok({ t: 'Café – 4%' }), 1024)).toEqual({ t: 'Café – 4%' });
  });
});

describe('callers', () => {
  it('conversation list retries a 429 and reports an oversized page as response_too_large', async () => {
    vi.useFakeTimers();
    const seq = [r429({ 'retry-after': '1' }), ok({ items: [] })];
    const api = new ConversationsApi('', undefined, (async () => seq.shift()!) as never);
    const p = api.list();
    await vi.advanceTimersByTimeAsync(1000);
    expect(await p).toEqual({ items: [] });
    vi.useRealTimers();
    const big = new Response('{"items":[]}', { headers: { 'content-length': String(9 * 1024 * 1024) } });
    const api2 = new ConversationsApi('', undefined, (async () => big) as never);
    await expect(api2.list()).rejects.toMatchObject({ code: 'response_too_large' });
    await expect(api2.list().catch((e) => e)).resolves.toBeInstanceOf(ConversationsApiError);
  });

  it('capabilities: a 429 is waited out instead of silently falling back to the demo set', async () => {
    vi.useFakeTimers();
    const real = { ...DEMO_CAPABILITIES, demo: false, feedbackReasons: [] };
    const seq = [r429({ 'retry-after': '1' }), ok(real)];
    const p = loadCapabilities('', (async () => seq.shift()!) as never);
    await vi.advanceTimersByTimeAsync(1000);
    expect(await p).toEqual(real);
    vi.useRealTimers();
  });
});
