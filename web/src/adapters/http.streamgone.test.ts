import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpAnalystAdapter } from './http';
import { MemoryTranscriptStore } from './memoryStore';
import { useChat } from '../lib/useChat';
import type { AnalystAdapter, AnyStreamEvent } from './types';
import type { AssistantMessage } from '../lib/contract';

// CONTRACT.md §8 (1.1+): a dropped stream re-attaches with Last-Event-ID; if the server answers 404
// `stream_gone` (the run finished > 60 s ago), the client reloads the conversation instead of
// showing "Connection lost".
afterEach(() => { vi.unstubAllGlobals(); });
const enc = new TextEncoder();
const partial = () => new Response(new ReadableStream({ start(c) {
  c.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.4.0', seq: 0 })}\n\n`));
  c.close(); // dropped before message.end
} }), { status: 200 });
const gone = () => new Response(JSON.stringify({ error: { code: 'stream_gone', message: 'Nothing to resume.' } }), { status: 404 });

const end = () => new Response(new ReadableStream({ start(c) {
  c.enqueue(enc.encode(`data: ${JSON.stringify({ type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 1 })}\n\n`));
  c.close();
} }), { status: 200 });
const run = async (a: HttpAnalystAdapter) => { const out: string[] = []; for await (const e of a.streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: 'q' }], signal: new AbortController().signal })) out.push(e.type); return out; };

describe('resume (contract 1.1)', () => {
  it('re-attaches with GET /api/chat/{id}/stream + Last-Event-ID', async () => {
    const calls: { url: string; method?: string; lei?: string }[] = [];
    const res = [partial(), end()];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => { calls.push({ url, method: init.method, lei: (init.headers as Record<string, string>)['last-event-id'] }); return res.shift()!; }));
    expect(await run(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1 }))).toEqual(['message.start', 'message.end']);
    expect(calls[1]).toEqual({ url: '/api/chat/m1/stream', method: 'GET', lei: 'm1:0' });
  });

  it('falls back to the 1.0 POST replay when the GET route is missing', async () => {
    const calls: { url: string; method?: string }[] = [];
    const res = [partial(), new Response('Not Found', { status: 404 }), end()];
    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit) => { calls.push({ url, method: init.method }); return res.shift()!; }));
    expect(await run(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1 }))).toEqual(['message.start', 'message.end']);
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual(['POST /api/chat', 'GET /api/chat/m1/stream', 'POST /api/chat']);
  });
});

describe('stream_gone', () => {
  it('http: a 404 on replay throws StreamGoneError (after sending Last-Event-ID)', async () => {
    const heads: Record<string, string>[] = [];
    const res = [partial(), gone()];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { heads.push(init.headers as Record<string, string>); return res.shift()!; }));
    const a = new HttpAnalystAdapter('', undefined, { retryBaseMs: 1 });
    const err = await (async () => { for await (const _ of a.streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: 'q' }], signal: new AbortController().signal })) void _; })().catch((e) => e);
    expect(heads[1]['last-event-id']).toBe('m1:0');
    expect((err as Error).name).toBe('StreamGoneError');
  });

  it('useChat: shows the stored answer instead of "Connection lost"', async () => {
    const store = new MemoryTranscriptStore();
    const stored: AssistantMessage = { id: 'm1', role: 'assistant', createdAt: '2026-10-25T00:00:00Z', parts: [{ type: 'text', id: 't', text: 'Stored answer.' }] as never };
    const adapter = {
      name: 'fake',
      async *streamTurn({ conversationId }: { conversationId: string }) {
        yield { type: 'message.start', messageId: 'm1', conversationId, contractVersion: '1.4.0', seq: 0 } as unknown as AnyStreamEvent;
        await store.appendMessage(conversationId, stored); // the server finished the run meanwhile
        const e = new Error('stream_gone'); e.name = 'StreamGoneError';
        throw e;
      },
      fetchBlob: async () => new Blob(),
    } as unknown as AnalystAdapter;
    const { result } = renderHook(() => useChat(adapter, store));
    await act(async () => { await result.current.send('q'); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const last = result.current.messages.at(-1) as AssistantMessage;
    expect(last.parts.map((p) => p.type)).toEqual(['text']);
    expect(JSON.stringify(last.parts)).toContain('Stored answer.');
  });

  it('useChat: falls back to "Connection lost" when the store has no copy', async () => {
    const adapter = {
      name: 'fake',
      async *streamTurn({ conversationId }: { conversationId: string }) {
        yield { type: 'message.start', messageId: 'm1', conversationId, contractVersion: '1.4.0', seq: 0 } as unknown as AnyStreamEvent;
        const e = new Error('stream_gone'); e.name = 'StreamGoneError';
        throw e;
      },
      fetchBlob: async () => new Blob(),
    } as unknown as AnalystAdapter;
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    await act(async () => { await result.current.send('q'); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(JSON.stringify((result.current.messages.at(-1) as AssistantMessage).parts)).toContain('Connection lost');
  });
});
