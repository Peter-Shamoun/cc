/**
 * Offline/online flapping (wifi handoff, elevator, tethering). The browser fires 'online' before the network actually
 * carries traffic, so the first fetch after it often fails. A flap must not burn the bounded reconnect budget or end
 * the answer with "Connection lost", and the offline banner must not flicker on sub-second blips.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';
import { HttpAnalystAdapter } from './http';
import { useSettledOnline } from '../lib/useOnline';

const enc = new TextEncoder();
const turn = () => ({ conversationId: 'c', messages: [{ id: 'u', role: 'user' as const, text: 'q' }], signal: new AbortController().signal });
const drain = async (it: AsyncIterable<unknown>) => { const out: { type: string }[] = []; for await (const e of it) out.push(e as { type: string }); return out; };
let online = true;
const setOnline = (v: boolean) => { online = v; Object.defineProperty(navigator, 'onLine', { configurable: true, get: () => online }); window.dispatchEvent(new Event(v ? 'online' : 'offline')); };
const sse = (evs: object[]) => new Response(new ReadableStream({ start(c) { for (const e of evs) c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); c.close(); } }), { status: 200 });
const start = { type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.0.0', seq: 0 };
const delta = (seq: number) => ({ type: 'part', part: { type: 'text', id: 't', text: `x${seq}` }, seq });
const end = (seq: number) => ({ type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq });
const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); setOnline(true); });

describe('offline/online flapping: stream reconnect', () => {
  it('a failed first fetch right after "online" does not use up the retry budget', async () => {
    const calls: string[] = [];
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async () => {
      n++;
      calls.push(online ? 'on' : 'off');
      if (n === 1) { setOnline(false); return sse([start]); } // drops, offline
      if (n === 2) throw new TypeError('Failed to fetch'); // 'online' fired, network not up yet
      return sse([end(1)]);
    }));
    const states: string[] = [];
    const p = drain(new HttpAnalystAdapter('', undefined, { maxRetries: 0, retryBaseMs: 2 }).streamTurn({ ...turn(), onConnection: (s: string) => states.push(s) } as never));
    await tick(20);
    setOnline(true);
    const out = await p;
    expect(out.at(-1)).toMatchObject({ type: 'message.end', finishReason: 'stop' });
    expect(n).toBe(3);
    expect(states.at(-1)).toBe('live');
  });

  it('survives five flaps in one answer with a zero retry budget, never duplicating replayed events', async () => {
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async () => {
      n++;
      if (n % 2 === 0) throw new TypeError('Failed to fetch'); // every reconnect's first try fails
      const k = (n - 1) / 2; // 0..5
      if (k < 5) { setOnline(false); return sse([...(k === 0 ? [start] : []), delta(k + 1), delta(k + 1)]); }
      return sse([delta(5), end(6)]); // overlap with what we already have
    }));
    const p = drain(new HttpAnalystAdapter('', undefined, { maxRetries: 0, retryBaseMs: 2 }).streamTurn(turn()));
    for (let i = 0; i < 5; i++) { await tick(15); setOnline(true); await tick(15); }
    const out = await p;
    const seqs = out.map((e) => (e as { seq?: number }).seq);
    expect(seqs).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(out.at(-1)).toMatchObject({ type: 'message.end', finishReason: 'stop' });
  });

  it('the free retry is per real offline period: failing while online still ends after maxRetries', async () => {
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async () => { n++; if (n === 1) return sse([start]); throw new TypeError('Failed to fetch'); }));
    await expect(drain(new HttpAnalystAdapter('', undefined, { maxRetries: 2, retryBaseMs: 2 }).streamTurn(turn()))).rejects.toThrow();
    expect(n).toBe(3); // the stream, then exactly maxRetries reconnects: no free retry without an offline period
  });
});

describe('offline banner hysteresis', () => {
  it('a sub-second blip never shows the banner; a real outage does; reconnect hides it at once', () => {
    vi.useFakeTimers();
    const { result, unmount } = renderHook(() => useSettledOnline(1000));
    expect(result.current).toBe(true);
    act(() => setOnline(false));
    act(() => { vi.advanceTimersByTime(400); });
    act(() => setOnline(true));
    act(() => { vi.advanceTimersByTime(2000); });
    expect(result.current).toBe(true); // blip ignored
    act(() => setOnline(false));
    act(() => { vi.advanceTimersByTime(999); });
    expect(result.current).toBe(true);
    act(() => { vi.advanceTimersByTime(1); });
    expect(result.current).toBe(false); // outage shown
    act(() => setOnline(true));
    expect(result.current).toBe(true); // back: hide immediately, no stale "offline"
    unmount();
  });

  it('starts offline when the page loads offline (no delay before the first paint)', () => {
    setOnline(false);
    const { result } = renderHook(() => useSettledOnline(1000));
    expect(result.current).toBe(false);
  });
});
