import { describe, expect, it, vi } from 'vitest';
import { ConversationsApi, ConversationsApiError } from './conversationsApi';

function fake(status = 200, body: unknown = {}) {
  const calls: { url: string; init: RequestInit }[] = [];
  const impl = vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  });
  return { calls, impl: impl as unknown as typeof fetch };
}

describe('ConversationsApi', () => {
  it('search posts the text in the body, never the URL', async () => {
    const f = fake(200, { items: [] });
    await new ConversationsApi('', 'demo:acme_labs:u', f.impl).search('Zelda Quixote');
    expect(f.calls[0].url).toBe('/api/conversations/search');
    expect(f.calls[0].url).not.toContain('Zelda');
    expect(f.calls[0].init.method).toBe('POST');
    expect(JSON.parse(String(f.calls[0].init.body))).toEqual({ q: 'Zelda Quixote' });
    expect(f.calls[0].init.cache).toBe('no-store');
    expect((f.calls[0].init.headers as Record<string, string>).authorization).toBe('Bearer demo:acme_labs:u');
  });
  it('maps 404 to undefined/false and other failures to typed errors', async () => {
    const api = new ConversationsApi('', undefined, fake(404, { error: { code: 'not_found', message: 'x' } }).impl);
    expect(await api.get('c1')).toBeUndefined();
    expect(await api.remove('c1')).toBe(false);
    const gone = new ConversationsApi('', undefined, fake(410, { error: { code: 'transcripts_unavailable', message: 'x' } }).impl);
    await expect(gone.list()).rejects.toMatchObject({ status: 410, code: 'transcripts_unavailable' });
    await expect(gone.list()).rejects.toBeInstanceOf(ConversationsApiError);
  });
  it('uses the documented verbs and paths', async () => {
    const f = fake(200, {});
    const api = new ConversationsApi('', undefined, f.impl);
    await api.list('50'); await api.rename('c/1', 'T'); await api.removeAll(); await api.setRetention(30); await api.info();
    expect(f.calls.map((c) => `${c.init.method} ${c.url}`)).toEqual([
      'GET /api/conversations?cursor=50', 'PATCH /api/conversations/c%2F1', 'DELETE /api/conversations',
      'PUT /api/transcripts/retention', 'GET /api/transcripts',
    ]);
  });
});
