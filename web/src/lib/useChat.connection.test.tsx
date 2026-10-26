import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { RECONNECT_QUIET_MS, useChat } from './useChat';
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

  describe('calm reconnects (ChatGPT/Claude): a short blip shows and announces nothing', () => {
    const setup = () => {
      let hook!: (s: ConnectionState) => void;
      let release!: () => void;
      const gate = new Promise<void>((r) => { release = r; });
      const adapter = {
        name: 'fake',
        async *streamTurn({ onConnection }: { onConnection?: (s: ConnectionState) => void }) {
          hook = onConnection!;
          yield start as unknown as AnyStreamEvent;
          yield { type: 'status', label: 'Thinking', seq: 1 } as unknown as AnyStreamEvent;
          await gate;
          yield { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 2 } as unknown as AnyStreamEvent;
        },
        fetchBlob: async () => new Blob(),
      } as unknown as AnalystAdapter;
      const store = new MemoryTranscriptStore(); // one store for the hook's lifetime (a new one per render resets useChat)
      const { result } = renderHook(() => useChat(adapter, store));
      const seen: (string | null | undefined)[] = [];
      const status = () => { const s = (result.current.messages[1] as AssistantViewV1 | undefined)?.status; seen.push(s); return s; };
      return { result, status, seen, hook: (s: ConnectionState) => hook(s), release: () => release() };
    };
    const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

    it('a drop that is back within the quiet window never shows "Reconnecting…"', async () => {
      const t = setup();
      let done!: Promise<void>;
      act(() => { done = t.result.current.send('q'); });
      await waitFor(() => expect(t.status()).toBe('Thinking'));
      act(() => t.hook('reconnecting'));
      expect(t.status()).toBe('Thinking');
      await act(async () => { await sleep(RECONNECT_QUIET_MS / 3); });
      expect(t.status()).toBe('Thinking');
      act(() => t.hook('live'));
      await act(async () => { await sleep(RECONNECT_QUIET_MS + 150); }); // the pending label was cancelled
      expect(t.status()).toBe('Thinking');
      expect(t.seen).not.toContain('Reconnecting…');
      await act(async () => { t.release(); await done; });
    });

    it('a reconnect that takes longer shows "Reconnecting…", then the server status again', async () => {
      const t = setup();
      let done!: Promise<void>;
      act(() => { done = t.result.current.send('q'); });
      await waitFor(() => expect(t.status()).toBe('Thinking'));
      act(() => t.hook('reconnecting'));
      await waitFor(() => expect(t.status()).toBe('Reconnecting…'), { timeout: RECONNECT_QUIET_MS + 1000 });
      act(() => t.hook('reconnecting')); // the next attempt keeps the label, no flicker back to the server status
      expect(t.status()).toBe('Reconnecting…');
      act(() => t.hook('live'));
      expect(t.status()).toBe('Thinking');
      await act(async () => { t.release(); await done; });
    });

    it('the turn ending while a reconnect is pending leaves no status behind', async () => {
      const t = setup();
      let done!: Promise<void>;
      act(() => { done = t.result.current.send('q'); });
      await waitFor(() => expect(t.status()).toBe('Thinking'));
      act(() => t.hook('reconnecting'));
      await act(async () => { t.release(); await done; });
      await act(async () => { await sleep(RECONNECT_QUIET_MS + 150); });
      expect(t.status()).toBeNull();
      expect(t.result.current.busy).toBe(false);
    });
  });
});
