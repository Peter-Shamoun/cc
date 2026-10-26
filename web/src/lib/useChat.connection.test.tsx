import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useChat } from './useChat';
import { MemoryTranscriptStore } from '../adapters/memoryStore';
import { HttpAnalystAdapter } from '../adapters/http';
import type { AnalystAdapter, AnyStreamEvent, ConnectionState } from '../adapters/types';
import type { AssistantViewV1 } from '../api/contract';

afterEach(() => vi.unstubAllGlobals());
const enc = new TextEncoder();
const sse = (evs: object[]) => new Response(new ReadableStream({ start(c) { for (const e of evs) c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); c.close(); } }), { status: 200 });
const start = { type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.0.0', seq: 0 };

describe('connection state while streaming', () => {
  it('http reports reconnecting, then live once the replay resumes', async () => {
    const states: ConnectionState[] = [];
    const responses = [sse([start]), sse([{ type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 1 }])];
    vi.stubGlobal('fetch', vi.fn(async () => responses.shift()!));
    const it = new HttpAnalystAdapter('', undefined, { retryBaseMs: 1 }).streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: 'q' }], signal: new AbortController().signal, onConnection: (s) => states.push(s) });
    for await (const _ of it) void _;
    expect(states).toEqual(['reconnecting', 'live']);
  });

  it('useChat shows the hiccup in the status line and restores the server status after', async () => {
    let hook!: (s: ConnectionState) => void;
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const adapter: AnalystAdapter = {
      name: 'fake',
      async *streamTurn({ onConnection }) {
        hook = onConnection!;
        yield start as unknown as AnyStreamEvent;
        yield { type: 'status', label: 'Thinking', seq: 1 } as unknown as AnyStreamEvent;
        await gate;
        yield { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 2 } as unknown as AnyStreamEvent;
      },
      fetchBlob: async () => new Blob(),
    } as AnalystAdapter;
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    let done!: Promise<void>;
    act(() => { done = result.current.send('q'); });
    const status = () => (result.current.messages[1] as AssistantViewV1 | undefined)?.status;
    await waitFor(() => expect(status()).toBe('Thinking'));
    act(() => hook('offline'));
    expect(status()).toMatch(/Offline/);
    act(() => hook('reconnecting'));
    expect(status()).toBe('Reconnecting…');
    act(() => hook('live'));
    expect(status()).toBe('Thinking');
    await act(async () => { release(); await done; });
    expect(result.current.busy).toBe(false);
  });
});
