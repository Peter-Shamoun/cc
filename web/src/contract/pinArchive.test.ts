import { describe, expect, it } from 'vitest';
import { ConversationsApi } from '../adapters/conversationsApi';
import { DEMO_CAPABILITIES } from './capabilities';

type Call = { url: string; method: string; body: unknown };

function fake(reply: unknown = { id: 'c_1', title: 'New analysis', updatedAt: '2026-10-25T00:00:00Z', pinned: true, archived: false }) {
  const calls: Call[] = [];
  const fetchImpl = (async (url: string, init: RequestInit) => {
    calls.push({ url, method: init.method ?? 'GET', body: init.body ? JSON.parse(String(init.body)) : undefined });
    return new Response(JSON.stringify(reply), { status: 200, headers: { 'content-type': 'application/json' } });
  }) as unknown as typeof fetch;
  return { api: new ConversationsApi('', 'Bearer t', fetchImpl), calls };
}

describe('1.17.0 pin / archive client', () => {
  it('setPinned / setArchived PATCH only their flag', async () => {
    const { api, calls } = fake();
    const out = await api.setPinned('c/1', true);
    await api.setArchived('c_2', false);
    expect(calls).toEqual([
      { url: '/api/conversations/c%2F1', method: 'PATCH', body: { pinned: true } },
      { url: '/api/conversations/c_2', method: 'PATCH', body: { archived: false } },
    ]);
    expect(out.pinned).toBe(true);
  });
  it('rename keeps its 1.3 body', async () => {
    const { api, calls } = fake();
    await api.rename('c_1', 'Payer mix');
    expect(calls[0].body).toEqual({ title: 'Payer mix' });
  });
  it('list and search pass the archived filter; omitted means the old request', async () => {
    const { api, calls } = fake({ items: [] });
    await api.list();
    await api.list('50', 'exclude');
    await api.search('denials');
    await api.search('denials', undefined, 'only');
    expect(calls.map((c) => c.url)).toEqual(['/api/conversations', '/api/conversations?cursor=50&archived=exclude',
      '/api/conversations/search', '/api/conversations/search']);
    expect(calls[2].body).toEqual({ q: 'denials' });
    expect(calls[3].body).toEqual({ q: 'denials', archived: 'only' });
  });
  it('demo capabilities announce server-side pin/archive', () => {
    expect(DEMO_CAPABILITIES.pinArchive).toBe(true);
  });
});
