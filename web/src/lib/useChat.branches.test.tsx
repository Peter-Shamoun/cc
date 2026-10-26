import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useChat } from './useChat';
import { MockAnalystAdapter } from '../adapters/mock';
import { MemoryTranscriptStore } from '../adapters/memoryStore';
import { HttpAnalystAdapter } from '../adapters/http';
import type { TranscriptStore } from '../adapters/types';
import type { Message } from './contract';

afterEach(() => vi.unstubAllGlobals());
const turn = { conversationId: 'c', signal: new AbortController().signal };
const drain = async (it: AsyncIterable<unknown>) => { for await (const _ of it) void _; };

describe('server branches (regenerate / edit, CONTRACT.md §8)', () => {
  it('http sends regenerate and user parentId; a 404 regenerate falls back to asking again', async () => {
    const bodies: { regenerate?: unknown; messages: { parentId?: string }[] }[] = [];
    const responses = [new Response('{"error":{"code":"validation","message":"Nothing to regenerate."}}', { status: 404 }), new Response('', { status: 500 })];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { bodies.push(JSON.parse(init.body as string)); return responses.shift()!; }));
    await drain(new HttpAnalystAdapter('').streamTurn({ ...turn, messages: [{ id: 'u2e', role: 'user', text: 'q', parentId: 'a1' }], regenerate: { messageId: 'm_1' } }));
    expect(bodies[0].regenerate).toEqual({ messageId: 'm_1' });
    expect(bodies[0].messages[0].parentId).toBe('a1');
    expect(bodies[1].regenerate).toBeUndefined();
  });

  it('useChat.regenerate re-runs a server-known answer with ChatRequest.regenerate', async () => {
    const adapter = new MockAnalystAdapter(0);
    const spy = vi.spyOn(adapter, 'streamTurn');
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    await act(async () => { await result.current.send('Show denial rate by payer'); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const first = result.current.messages[1].id;
    await act(async () => { await result.current.regenerate(); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(spy.mock.calls.at(-1)![0].regenerate).toEqual({ messageId: first });
    expect(result.current.messages).toHaveLength(2);
  });

  it('editAt sends the edit with parentId = the message before it', async () => {
    const adapter = new MockAnalystAdapter(0);
    const spy = vi.spyOn(adapter, 'streamTurn');
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    await act(async () => { await result.current.send('first question'); });
    await act(async () => { await result.current.send('second question'); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const before = result.current.messages[1].id;
    await act(async () => { await result.current.editAt(2, 'second question, edited'); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const sent = spy.mock.calls.at(-1)![0].messages.at(-1)!;
    expect(sent).toMatchObject({ role: 'user', text: 'second question, edited', parentId: before, editOf: expect.any(String) });
  });

  it('editing the first question sends editOf and no parentId (contract 1.2: a new root)', async () => {
    const adapter = new MockAnalystAdapter(0);
    const spy = vi.spyOn(adapter, 'streamTurn');
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    await act(async () => { await result.current.send('first question'); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const original = result.current.messages[0].id;
    await act(async () => { await result.current.editAt(0, 'first question, edited'); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const sent = spy.mock.calls.at(-1)![0].messages;
    expect(sent).toHaveLength(1);
    expect(sent[0]).toMatchObject({ role: 'user', text: 'first question, edited', editOf: original });
    expect(sent[0].parentId).toBeUndefined();
    expect(result.current.versionOf(0)).toEqual({ current: 2, total: 2 });
  });

  it('http sends editOf on the wire', async () => {
    const bodies: { messages: { editOf?: string; parentId?: string }[] }[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { bodies.push(JSON.parse(init.body as string)); return new Response('', { status: 500 }); }));
    await drain(new HttpAnalystAdapter('').streamTurn({ ...turn, messages: [{ id: 'u1e', role: 'user', text: 'q', editOf: 'u1' }] })).catch(() => undefined);
    expect(bodies[0].messages[0]).toEqual({ id: 'u1e', role: 'user', text: 'q', editOf: 'u1' });
  });

  it('open() folds an append-only server transcript to the newest path with edit versions', async () => {
    const at = '2026-10-25T00:00:00Z';
    const msgs = [
      { id: 'u1', role: 'user', text: 'q1', createdAt: at },
      { id: 'a1', role: 'assistant', parts: [{ type: 'text', id: 't', text: 'old' }], createdAt: at, parentId: 'u1' },
      { id: 'a1b', role: 'assistant', parts: [{ type: 'text', id: 't', text: 'new' }], createdAt: at, parentId: 'u1' },
      { id: 'u2', role: 'user', text: 'q2', createdAt: at },
      { id: 'a2', role: 'assistant', parts: [], createdAt: at, parentId: 'u2' },
      { id: 'u2e', role: 'user', text: 'q2 edited', createdAt: at, parentId: 'a1b' },
      { id: 'a2e', role: 'assistant', parts: [], createdAt: at, parentId: 'u2e' },
    ] as Message[];
    const store = new MemoryTranscriptStore() as TranscriptStore;
    vi.spyOn(store, 'getConversation').mockResolvedValue({ id: 'c1', messages: msgs });
    const adapter = new MockAnalystAdapter(0);
    const { result } = renderHook(() => useChat(adapter, store));
    await act(async () => { await result.current.open('c1'); });
    expect(result.current.messages.map((m) => m.id)).toEqual(['u1', 'a1b', 'u2e', 'a2e']);
    expect(result.current.versionOf(2)).toEqual({ current: 2, total: 2 });
  });
});
