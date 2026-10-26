import { describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpTranscriptStore } from './httpStore';
import { useChat } from '../lib/useChat';
import { MockAnalystAdapter } from './mock';

// Contract 1.17.0: pin/archive persisted server-side (PATCH flags, ?archived= filter, Capabilities.pinArchive).
type Conv = { id: string; title: string; updatedAt: string; messageCount: number; pinned: boolean; archived: boolean };
function fakeServer(convs: Conv[], { failPatch = false } = {}) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  let release: (() => void) | null = null;
  const impl = vi.fn(async (url: string, init: RequestInit) => {
    const method = init.method ?? 'GET';
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    const json = (status: number, out: unknown) => new Response(JSON.stringify(out), { status, headers: { 'content-type': 'application/json' } });
    if (url === '/api/transcripts') return json(200, { mode: 'persisted', persisted: true, retentionDays: 90 });
    if (method === 'GET' && url.startsWith('/api/conversations')) {
      const f = new URLSearchParams(url.split('?')[1] ?? '').get('archived') ?? 'include';
      const items = convs.filter((c) => (f === 'include' ? true : f === 'only' ? c.archived : !c.archived)).sort((a, b) => Number(b.pinned) - Number(a.pinned));
      return json(200, { items });
    }
    if (method === 'PATCH') {
      await new Promise<void>((r) => { release = r; });
      if (failPatch) return json(503, { error: { code: 'unavailable' } });
      const c = convs.find((x) => url.endsWith(x.id))!;
      if ('pinned' in body) { c.pinned = body.pinned; if (body.pinned) c.archived = false; }
      if ('archived' in body) { c.archived = body.archived; if (body.archived) c.pinned = false; }
      return json(200, { id: c.id, title: c.title, updatedAt: c.updatedAt, pinned: c.pinned, archived: c.archived });
    }
    return json(404, { error: { code: 'not_found' } });
  });
  return { calls, impl: impl as unknown as typeof fetch, release: () => release?.() };
}
const conv = (id: string, extra: Partial<Conv> = {}): Conv => ({ id, title: `Chat ${id}`, updatedAt: '2026-10-25T00:00:00Z', messageCount: 2, pinned: false, archived: false, ...extra });

describe('HttpTranscriptStore pin/archive (contract 1.17.0)', () => {
  it('without the capability: no pin/archive methods and the plain list', async () => {
    const s = fakeServer([conv('a'), conv('b', { archived: true })]);
    const store = new HttpTranscriptStore('', 'demo:acme_labs:u', s.impl);
    expect(store.setPinned).toBeUndefined();
    expect(store.setArchived).toBeUndefined();
    const { items } = await store.listConversations();
    expect(items.map((i) => i.id)).toEqual(['a', 'b']);
    expect(items[0]).not.toHaveProperty('pinned');
    expect(s.calls.filter((c) => c.url.startsWith('/api/conversations')).map((c) => c.url)).toEqual(['/api/conversations']);
  });

  it('with it: the sidebar page excludes archived, the archived ones come from ?archived=only, flags are mapped', async () => {
    const s = fakeServer([conv('a'), conv('b', { archived: true }), conv('c', { pinned: true })]);
    const store = new HttpTranscriptStore('', 'demo:acme_labs:u', s.impl);
    store.enablePinArchive(true);
    const { items } = await store.listConversations();
    expect(items.map((i) => [i.id, i.pinned, i.archived])).toEqual([['c', true, false], ['a', false, false], ['b', false, true]]);
    expect(s.calls.map((c) => c.url)).toEqual(expect.arrayContaining(['/api/conversations?archived=exclude', '/api/conversations?archived=only']));
    // A later page only asks for the next sidebar page.
    s.calls.length = 0;
    await store.listConversations('cur1');
    expect(s.calls.map((c) => c.url)).toEqual(['/api/conversations?cursor=cur1&archived=exclude']);
    store.enablePinArchive(false);
    expect(store.setPinned).toBeUndefined();
  });

  it('pin and archive are PATCHes with the flag only', async () => {
    const s = fakeServer([conv('a')]);
    const store = new HttpTranscriptStore('', 'demo:acme_labs:u', s.impl);
    store.enablePinArchive(true);
    const p = store.setPinned!('a', true); s.release(); await p;
    await new Promise((r) => setTimeout(r, 0));
    const a = store.setArchived!('a', true);
    await waitFor(() => expect(s.calls.filter((c) => c.method === 'PATCH')).toHaveLength(2));
    s.release(); await a;
    expect(s.calls.filter((c) => c.method === 'PATCH').map((c) => [c.url, c.body])).toEqual([
      ['/api/conversations/a', { pinned: true }],
      ['/api/conversations/a', { archived: true }],
    ]);
  });
});

describe('useChat pin/archive are optimistic with rollback', () => {
  it('the row moves before the server answers, then matches the server', async () => {
    const s = fakeServer([conv('a'), conv('b')]);
    const store = new HttpTranscriptStore('', 'demo:acme_labs:u', s.impl);
    store.enablePinArchive(true);
    const { result } = renderHook(() => useChat(new MockAnalystAdapter(0), store));
    await waitFor(() => expect(result.current.threads).toHaveLength(2));
    let done!: Promise<void>;
    act(() => { done = result.current.pin('b', true); });
    expect(result.current.threads.find((t) => t.id === 'b')?.pinned).toBe(true); // before the PATCH returns
    await waitFor(() => expect(s.calls.some((c) => c.method === 'PATCH')).toBe(true));
    s.release();
    await act(async () => { await done; });
    await waitFor(() => expect(result.current.threads[0].id).toBe('b'));
    act(() => { done = result.current.archive('b', true); });
    const b = result.current.threads.find((t) => t.id === 'b');
    expect([b?.archived, b?.pinned]).toEqual([true, false]); // archiving drops the pin, at once
    await waitFor(() => expect(s.calls.filter((c) => c.method === 'PATCH')).toHaveLength(2));
    s.release();
    await act(async () => { await done; });
    await waitFor(() => expect(result.current.threads.find((t) => t.id === 'b')?.archived).toBe(true));
  });

  it('a refused PATCH puts the row back and rejects so the shell can say so', async () => {
    const s = fakeServer([conv('a'), conv('b')], { failPatch: true });
    const store = new HttpTranscriptStore('', 'demo:acme_labs:u', s.impl);
    store.enablePinArchive(true);
    const { result } = renderHook(() => useChat(new MockAnalystAdapter(0), store));
    await waitFor(() => expect(result.current.threads).toHaveLength(2));
    let done!: Promise<void>;
    act(() => { done = result.current.archive('a', true); });
    expect(result.current.threads.find((t) => t.id === 'a')?.archived).toBe(true);
    await waitFor(() => expect(s.calls.some((c) => c.method === 'PATCH')).toBe(true));
    s.release();
    await act(async () => { await expect(done).rejects.toBeTruthy(); });
    expect(result.current.threads.find((t) => t.id === 'a')?.archived).toBe(false);
  });
});
