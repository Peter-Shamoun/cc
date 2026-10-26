import { describe, expect, it, vi } from 'vitest';
import { getShare, loadShared, revokeShare, setShare, shareIdFromPath, shareUrl, sharedUrl } from './share';

const reply = (status: number, body: unknown) =>
  Promise.resolve(new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
const LINK = { shareId: 'sh_x', conversationId: 'c_1', url: '/shared/sh_x', scope: 'organization', includeData: false, createdAt: '2026-10-25T20:00:00Z' };

describe('share (1.11.0)', () => {
  it('builds urls and parses app paths', () => {
    expect(shareUrl('c 1')).toBe('/api/conversations/c%201/share');
    expect(sharedUrl('sh_x', '/b')).toBe('/b/api/shared/sh_x');
    expect(shareIdFromPath('/shared/sh_Ab-9')).toBe('sh_Ab-9');
    expect(shareIdFromPath('/shared/../x')).toBeNull();
  });
  it('PUTs includeData (default false) with the caller headers', async () => {
    const fetchImpl = vi.fn(() => reply(200, LINK));
    await expect(setShare('c_1', undefined, { fetchImpl, init: { headers: { 'x-cc-csrf': '1' } } })).resolves.toEqual(LINK);
    const [url, init] = fetchImpl.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/conversations/c_1/share');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({ includeData: false });
    expect(init.headers).toMatchObject({ 'x-cc-csrf': '1', 'Content-Type': 'application/json' });
  });
  it('maps 404 to null / false and throws on other errors', async () => {
    const nf = () => reply(404, { error: { code: 'not_found', message: 'Not found.' } });
    await expect(getShare('c_1', { fetchImpl: nf })).resolves.toBeNull();
    await expect(loadShared('sh_x', { fetchImpl: nf })).resolves.toBeNull();
    await expect(revokeShare('c_1', { fetchImpl: nf })).resolves.toBe(false);
    await expect(setShare('c_1', true, { fetchImpl: nf })).rejects.toThrow('404');
    await expect(getShare('c_1', { fetchImpl: () => reply(401, {}) })).rejects.toThrow('401');
  });
  it('revokes', async () => {
    await expect(revokeShare('c_1', { fetchImpl: () => reply(200, { revoked: true }) })).resolves.toBe(true);
  });
});
