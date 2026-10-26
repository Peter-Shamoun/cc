import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { markAnswered, useChat, type ViewMessage } from './useChat';
import { MockAnalystAdapter } from '../adapters/mock';
import { MemoryTranscriptStore } from '../adapters/memoryStore';
import { HttpAnalystAdapter } from '../adapters/http';
import { V1_FIXTURES } from '../fixtures/v1';
import type { AssistantViewV1 } from '../api/contract';

/** An SSE body from contract events (each already carries seq, as the v1 server sends them). */
function sse(events: object[], opts: { close?: boolean } = {}): Response {
  const enc = new TextEncoder();
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      for (const e of events) c.enqueue(enc.encode(`id: m1:${(e as { seq?: number }).seq ?? ''}\ndata: ${JSON.stringify(e)}\n\n`));
      if (opts.close !== false) c.close();
    },
  });
  return new Response(body, { status: 200, headers: { 'content-type': 'text/event-stream' } });
}
const start = { type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.0.0', seq: 0 };
const t = (seq: number, delta: string) => ({ type: 'text.delta', partId: 't1', delta, seq });
const end = (seq: number) => ({ type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 1, elapsedMs: 10 }, seq });
const drain = async (it: AsyncIterable<unknown>) => { const out: unknown[] = []; for await (const e of it) out.push(e); return out; };
const turn = (signal = new AbortController().signal) => ({ conversationId: 'c', messages: [{ id: 'u', role: 'user' as const, text: 'q' }], signal });

afterEach(() => vi.unstubAllGlobals());

describe('http adapter resilience (CONTRACT.md §6, §8)', () => {
  it('reconnects a dropped stream with Last-Event-ID and skips replayed events', async () => {
    const calls: RequestInit[] = [];
    const responses = [
      sse([start, { type: 'text.start', partId: 't1', seq: 1 }, t(2, 'Hel')]), // drops before message.end
      sse([t(2, 'Hel'), t(3, 'lo'), { type: 'text.end', partId: 't1', seq: 4 }, end(5)]), // server replays from seq>2 (one overlap)
    ];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { calls.push(init); return responses.shift()!; }));
    const out = (await drain(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1 }).streamTurn(turn()))) as { type: string; seq: number }[];
    expect(calls).toHaveLength(2);
    expect((calls[0].headers as Record<string, string>)['last-event-id']).toBeUndefined();
    expect((calls[1].headers as Record<string, string>)['last-event-id']).toBe('m1:2');
    expect(out.map((e) => e.seq)).toEqual([0, 1, 2, 3, 4, 5]);
    expect(out.at(-1)!.type).toBe('message.end');
  });

  it('gives up after maxRetries so useChat can show Connection lost', async () => {
    let n = 0;
    vi.stubGlobal('fetch', vi.fn(async () => (n++ === 0 ? sse([start]) : new Response('', { status: 503 }))));
    await expect(drain(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1, maxRetries: 2 }).streamTurn(turn()))).rejects.toThrow();
    expect(n).toBe(3);
  });

  it('falls back to a fresh question when the clarification expired (409)', async () => {
    const bodies: { resume?: unknown }[] = [];
    const responses = [new Response('{"error":{"code":"clarification_expired"}}', { status: 409 }), sse([start, end(1)])];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { bodies.push(JSON.parse(init.body as string)); return responses.shift()!; }));
    const out = await drain(new HttpAnalystAdapter('').streamTurn({ ...turn(), resume: { clarificationId: 'gone', choiceIds: ['c1'] } }));
    expect(bodies[0].resume).toEqual({ clarificationId: 'gone', choiceIds: ['c1'] });
    expect(bodies[1].resume).toBeUndefined();
    expect(out).toHaveLength(2);
  });

  it('after Stop, keeps reading until the server sends aborted message.end with usage', async () => {
    const enc = new TextEncoder();
    let push!: (e: object) => void;
    let close!: () => void;
    const body = new ReadableStream<Uint8Array>({ start(c) { push = (e) => c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); close = () => c.close(); } });
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string) => { urls.push(u); return u.endsWith('/stop') ? new Response('{"stopped":true}') : new Response(body, { status: 200 }); }));
    const ctrl = new AbortController();
    const it = new HttpAnalystAdapter('').streamTurn(turn(ctrl.signal))[Symbol.asyncIterator]();
    push(start);
    expect((await it.next()).value).toMatchObject({ type: 'message.start' });
    ctrl.abort();
    push({ type: 'message.end', messageId: 'm1', finishReason: 'aborted', usage: { queries: 2, elapsedMs: 900 }, seq: 1 });
    close();
    const last = (await it.next()).value as { finishReason: string; usage: { queries: number } };
    expect(last.finishReason).toBe('aborted');
    expect(last.usage.queries).toBe(2);
    expect(urls).toContain('/api/chat/m1/stop');
  });
});

describe('useChat plumbing', () => {
  it('marks a replied clarification answered after reload', async () => {
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(new MockAnalystAdapter(0), store));
    const clarify = V1_FIXTURES.find((f) => f.id === 'v1-clarify')!;
    await act(async () => { await result.current.send(clarify.prompt); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const asked = () => (result.current.messages[1] as AssistantViewV1).parts.find((p) => p.type === 'clarification') as { answered?: boolean };
    expect(asked().answered).toBeFalsy();
    await act(async () => { await result.current.send('Last 12 months', { resume: { clarificationId: 'clr_demo_1', choiceIds: ['c2'] } }); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(asked().answered).toBe(true);
    await act(async () => { await result.current.open(result.current.convId!); });
    expect(asked().answered).toBe(true);
  });

  it('markAnswered leaves the last message (still awaiting a reply) alone', () => {
    const a = { id: 'a', role: 'assistant', parts: [{ type: 'clarification', id: 'p', clarificationId: 'x', question: 'q', allowFreeText: true }], steps: [], escalations: [], lastSeq: -1, streaming: false, status: null, openText: [], createdAt: '' } as unknown as ViewMessage;
    expect((markAnswered([a])[0] as AssistantViewV1).parts[0]).not.toHaveProperty('answered');
  });

  it('keeps usage figures on an answer the user stops', async () => {
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(new MockAnalystAdapter(1), store));
    const prov = V1_FIXTURES.find((f) => f.id === 'v1-providers-repair')!;
    let done!: Promise<void>;
    act(() => { done = result.current.send(prov.prompt); });
    await waitFor(() => expect((result.current.messages[1] as AssistantViewV1 | undefined)?.steps.some((s) => s.kind === 'sql' && s.state !== 'running')).toBe(true), { timeout: 5000 });
    await act(async () => { result.current.stop(); await done; });
    const a = result.current.messages[1] as AssistantViewV1;
    expect(a.finishReason).toBe('aborted');
    expect(a.usage?.queries).toBeGreaterThanOrEqual(1);
    expect(a.usage?.elapsedMs).toBeGreaterThan(0);
    await act(async () => { await result.current.open(result.current.convId!); });
    expect((result.current.messages[1] as AssistantViewV1).usage?.queries).toBeGreaterThanOrEqual(1);
  });
});

describe('useChat run ownership and batching', () => {
  it('a run aborted by opening another chat never overwrites the newly shown chat', async () => {
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(new MockAnalystAdapter(2), store));
    const prov = V1_FIXTURES.find((f) => f.id === 'v1-providers-repair')!;
    let done!: Promise<void>;
    act(() => { done = result.current.send(prov.prompt); });
    await waitFor(() => expect(result.current.messages.length).toBe(2));
    await act(async () => { result.current.newChat(); await done; });
    expect(result.current.messages).toEqual([]);
    expect(result.current.busy).toBe(false);
  });

});
