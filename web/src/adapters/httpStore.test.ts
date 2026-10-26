import { describe, expect, it, vi } from 'vitest';
import { HttpTranscriptStore } from './httpStore';

type Route = (body: unknown) => [number, unknown];
function server(routes: Record<string, Route>) {
  const calls: { method: string; url: string; body?: unknown }[] = [];
  const impl = vi.fn(async (url: string, init: RequestInit) => {
    const method = init.method ?? 'GET';
    const body = init.body ? JSON.parse(String(init.body)) : undefined;
    calls.push({ method, url, body });
    const r = routes[`${method} ${url}`];
    const [status, out] = r ? r(body) : [404, { error: { code: 'not_found' } }];
    return new Response(JSON.stringify(out), { status, headers: { 'content-type': 'application/json' } });
  });
  return { calls, impl: impl as unknown as typeof fetch };
}

describe('HttpTranscriptStore', () => {
  it('reads the mode, lists a new chat before the server has it, then uses the server list with the client title', async () => {
    let serverHas = false;
    const s = server({
      'GET /api/transcripts': () => [200, { mode: 'persisted', persisted: true, retentionDays: 90 }],
      'GET /api/conversations': () => [200, { items: serverHas ? [{ id: 'c_1', title: 'New analysis', updatedAt: '2026-10-25T00:00:00Z', messageCount: 2 }] : [] }],
    });
    const store = new HttpTranscriptStore('', 'demo:acme_labs:u', s.impl);
    await store.ready;
    expect(store.mode).toBe('server-encrypted');
    await store.appendMessage('c_1', { id: 'u1', role: 'user', text: 'What was our denial rate?', createdAt: '' }, 'Denial rate');
    expect((await store.listConversations()).items.map((i) => [i.id, i.title])).toEqual([['c_1', 'Denial rate']]);
    serverHas = true;
    expect((await store.listConversations()).items).toHaveLength(1);
    expect((await store.listConversations()).items[0].title).toBe('Denial rate');
    // nothing about the question went into a URL
    expect(s.calls.every((c) => !/denial/i.test(c.url))).toBe(true);
  });

  it('searches with a POST body and maps snippets to highlight offsets', async () => {
    const s = server({
      'GET /api/transcripts': () => [200, { mode: 'ephemeral', persisted: false, retentionDays: null }],
      'POST /api/conversations/search': () => [200, { items: [{ id: 'c_2', title: 'Payers', updatedAt: 'x', messageCount: 2, snippet: '…the CO-197 share rose' }] }],
    });
    const store = new HttpTranscriptStore('', undefined, s.impl);
    const hits = await store.search('co-197');
    expect(s.calls.at(-1)).toMatchObject({ method: 'POST', url: '/api/conversations/search', body: { q: 'co-197' } });
    expect(hits[0].snippet.slice(hits[0].matchStart, hits[0].matchStart + hits[0].matchLength)).toBe('CO-197');
    expect((store as unknown as { setPinned?: unknown }).setPinned).toBeUndefined();
  });

  it('renames and deletes on the server', async () => {
    const s = server({
      'GET /api/transcripts': () => [200, { mode: 'ephemeral', persisted: false, retentionDays: null }],
      'PATCH /api/conversations/c_3': (b) => [200, { id: 'c_3', title: (b as { title: string }).title, updatedAt: 'x' }],
      'DELETE /api/conversations/c_3': () => [200, { deleted: true }],
    });
    const store = new HttpTranscriptStore('', undefined, s.impl);
    await store.renameConversation('c_3', 'Q3 denials');
    await store.deleteConversation('c_3');
    expect(s.calls.map((c) => `${c.method} ${c.url}`)).toEqual(['GET /api/transcripts', 'PATCH /api/conversations/c_3', 'DELETE /api/conversations/c_3']);
  });
});

describe('hydrate', () => {
  it('groups flat persisted step events into step views', async () => {
    const { hydrate } = await import('./httpStore');
    const m = hydrate({ id: 'a1', role: 'assistant', createdAt: '', parts: [], finishReason: 'stop',
      steps: [
        { type: 'step.explore', stepId: 's1', probe: 'describe_view', summary: '8 columns', elapsedMs: 60, target: 'sem.claims', seq: 1 },
        { type: 'step.sql_running', stepId: 's2', sql: 'select 1', purpose: 'answer', seq: 2 },
        { type: 'step.sql_result', stepId: 's2', rowCount: 1, elapsedMs: 5, seq: 3 },
      ] } as never) as unknown as { steps: { stepId: string; events: unknown[] }[] };
    expect(m.steps.map((s) => [s.stepId, s.events.length])).toEqual([['s1', 1], ['s2', 2]]);
  });
});
