import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpAnalystAdapter, retryAfterMs } from './http';
import { MemoryTranscriptStore } from './memoryStore';
import { rateLimitLabel, useChat } from '../lib/useChat';
import type { AnalystAdapter, AnyStreamEvent, ConnectionInfo, ConnectionState } from './types';
import type { AssistantViewV1 } from '../api/contract';

afterEach(() => { vi.unstubAllGlobals(); vi.useRealTimers(); });
const enc = new TextEncoder();
const sse = (evs: object[]) => new Response(new ReadableStream({ start(c) { for (const e of evs) c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); c.close(); } }), { status: 200 });
const ok = () => sse([
  { type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.0.0', seq: 0 },
  { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 1 },
]);
const limited = (retryAfter?: string) => new Response(JSON.stringify({ error: { code: 'rate_limited', message: '' } }), { status: 429, headers: retryAfter ? { 'retry-after': retryAfter } : {} });
const turn = (onConnection?: (s: ConnectionState, i?: ConnectionInfo) => void, signal = new AbortController().signal) => ({ conversationId: 'c', messages: [{ id: 'u', role: 'user' as const, text: 'q' }], signal, onConnection });
const drain = async (it: AsyncIterable<unknown>) => { const out: { type: string; part?: { code: string; retryAfterMs?: number } }[] = []; for await (const e of it) out.push(e as never); return out; };

describe('Retry-After parsing', () => {
  it('reads delay-seconds and HTTP dates; garbage is null', () => {
    expect(retryAfterMs('3')).toBe(3000);
    expect(retryAfterMs('0')).toBe(0);
    const now = Date.parse('2026-10-25T20:00:00Z');
    expect(retryAfterMs('Sun, 25 Oct 2026 20:00:05 GMT', now)).toBe(5000);
    expect(retryAfterMs('Sun, 25 Oct 2026 19:59:00 GMT', now)).toBe(0);
    expect(retryAfterMs(null)).toBeNull();
    expect(retryAfterMs('soon')).toBeNull();
  });
});

describe('429 auto-retry', () => {
  it('waits Retry-After, reports the countdown, then streams the answer', async () => {
    const calls: number[] = [];
    vi.stubGlobal('fetch', vi.fn(async () => { calls.push(Date.now()); return calls.length === 1 ? limited('0') : ok(); }));
    const states: [ConnectionState, ConnectionInfo | undefined][] = [];
    const out = await drain(new HttpAnalystAdapter('').streamTurn(turn((s, i) => states.push([s, i]))));
    expect(calls).toHaveLength(2);
    expect(states).toEqual([['rate_limited', { retryInMs: 0 }], ['live', undefined]]);
    expect(out.map((e) => e.type)).toEqual(['message.start', 'message.end']);
  });

  it('really waits the advertised time', async () => {
    vi.useFakeTimers();
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async () => (++n === 1 ? limited('3') : ok())));
    const p = drain(new HttpAnalystAdapter('').streamTurn(turn()));
    await vi.advanceTimersByTimeAsync(2900);
    expect(n).toBe(1);
    await vi.advanceTimersByTimeAsync(200);
    expect(n).toBe(2);
    expect((await p).at(-1)!.type).toBe('message.end');
  });

  it('gives up after maxRateRetries with the rate_limited error card (retryAfterMs kept)', async () => {
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async () => { n++; return limited('0'); }));
    const out = await drain(new HttpAnalystAdapter('', undefined, { maxRateRetries: 2 }).streamTurn(turn()));
    expect(n).toBe(3);
    expect(out[0].part).toMatchObject({ code: 'rate_limited' });
    expect(out[1]).toMatchObject({ type: 'message.end', finishReason: 'error' });
  });

  it('does not sit on a wait longer than maxRateWaitMs: shows the error at once', async () => {
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async () => { n++; return limited('120'); }));
    const out = await drain(new HttpAnalystAdapter('').streamTurn(turn()));
    expect(n).toBe(1);
    expect(out[0].part).toMatchObject({ code: 'rate_limited', retryAfterMs: 120_000 });
  });

  it('Stop during the wait aborts without another request', async () => {
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async () => { n++; return limited('5'); }));
    const ctrl = new AbortController();
    const p = drain(new HttpAnalystAdapter('').streamTurn(turn(undefined, ctrl.signal)));
    await new Promise((r) => setTimeout(r, 20));
    ctrl.abort();
    await expect(p).rejects.toMatchObject({ name: 'AbortError' });
    expect(n).toBe(1);
  });

  it('demo fault header goes on the first attempt only', async () => {
    const seen: (string | undefined)[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => {
      const h = init.headers as Record<string, string>;
      seen.push(h['x-demo-fault']);
      return seen.length === 1 ? limited('0') : ok();
    }));
    await drain(new HttpAnalystAdapter('', 'demo:t:u', { demoFault: 'rate_limited' }).streamTurn(turn()));
    expect(seen).toEqual(['rate_limited', undefined]);
  });

  it('no fault header without the option', async () => {
    const seen: (string | undefined)[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { seen.push((init.headers as Record<string, string>)['x-demo-fault']); return ok(); }));
    await drain(new HttpAnalystAdapter('').streamTurn(turn()));
    expect(seen).toEqual([undefined]);
  });
});

describe('useChat countdown', () => {
  it('labels count down', () => {
    expect(rateLimitLabel(3000)).toBe('Busy right now, retrying in 3s…');
    expect(rateLimitLabel(2100)).toBe('Busy right now, retrying in 3s…');
    expect(rateLimitLabel(0)).toBe('Busy right now, retrying…');
  });

  it('status line ticks down each second, then the server status takes over', async () => {
    let hook!: (s: ConnectionState, i?: ConnectionInfo) => void;
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const adapter = {
      name: 'fake',
      async *streamTurn({ onConnection }: { onConnection?: typeof hook }) {
        hook = onConnection!;
        await gate;
        yield { type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.0.0', seq: 0 } as unknown as AnyStreamEvent;
        yield { type: 'status', label: 'Thinking', seq: 1 } as unknown as AnyStreamEvent;
        yield { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 2 } as unknown as AnyStreamEvent;
      },
      fetchBlob: async () => new Blob(),
    } as unknown as AnalystAdapter;
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    let done!: Promise<void>;
    act(() => { done = result.current.send('q'); });
    await waitFor(() => expect(hook).toBeTypeOf('function'));
    vi.useFakeTimers();
    const status = () => (result.current.messages.at(-1) as AssistantViewV1).status;
    act(() => hook('rate_limited', { retryInMs: 3000 }));
    expect(status()).toBe('Busy right now, retrying in 3s…');
    act(() => { vi.advanceTimersByTime(1000); });
    expect(status()).toBe('Busy right now, retrying in 2s…');
    act(() => { vi.advanceTimersByTime(1000); });
    expect(status()).toBe('Busy right now, retrying in 1s…');
    act(() => hook('live'));
    expect(status()).toBeNull();
    act(() => { vi.advanceTimersByTime(3000); });
    expect(status()).toBeNull(); // the countdown stopped
    vi.useRealTimers();
    await act(async () => { release(); await done; });
    expect(result.current.busy).toBe(false);
  });
});
