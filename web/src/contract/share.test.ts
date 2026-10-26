import { describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { DEMO_CAPABILITIES } from './capabilities';
import {
  DEFAULT_SHARE_EXPIRY_DAYS, SHARE_EXPIRY_DAYS, getShare, loadShared, revokeShare, setShare, shareDaysLeft,
  shareIdFromPath, shareUrl, sharedUrl,
} from './share';
import type { ShareLink, SharedConversation } from './v1.gen';

const example: unknown = JSON.parse(readFileSync(resolve(process.cwd(), '../api/contract/examples/share.json'), 'utf8'));

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

describe('share (1.15.0: expiry, rotation, SQL withheld)', () => {
  it('sends expiresInDays only when chosen', async () => {
    const fetchImpl = vi.fn(() => reply(200, LINK));
    await setShare('c_1', false, { fetchImpl, expiresInDays: 7 });
    await setShare('c_1', true, { fetchImpl });
    const bodies = fetchImpl.mock.calls.map((c) => JSON.parse(String((c as unknown as [string, RequestInit])[1].body)));
    expect(bodies).toEqual([{ includeData: false, expiresInDays: 7 }, { includeData: true }]);
  });
  it('exposes the options and counts days left', () => {
    expect(SHARE_EXPIRY_DAYS).toEqual([7, 30, 90]);
    expect(DEFAULT_SHARE_EXPIRY_DAYS).toBe(30);
    const now = Date.parse('2026-10-25T20:00:00Z');
    expect(shareDaysLeft({ expiresAt: '2026-11-24T20:00:00Z' }, now)).toBe(30);
    expect(shareDaysLeft({ expiresAt: '2026-10-25T21:00:00Z' }, now)).toBe(1);
    expect(shareDaysLeft({ expiresAt: '2026-10-01T00:00:00Z' }, now)).toBe(0);
    expect(shareDaysLeft({}, now)).toBeNull();
    expect(shareDaysLeft({ expiresAt: 'nope' }, now)).toBeNull();
  });
  it('the example shows expiry, rotation on widening and withheld SQL', () => {
    const ex = example as unknown as {
      share: { response: ShareLink }; view: { response: SharedConversation }; widen: { response: ShareLink };
    };
    expect(ex.share.response.expiresInDays).toBe(30);
    expect(ex.view.response.sqlWithheld).toBe(true);
    expect(JSON.stringify(ex.view.response)).not.toMatch(/SELECT/);
    expect(ex.widen.response.rotated).toBe(true);
    expect(ex.widen.response.shareId).not.toBe(ex.share.response.shareId);
    expect(DEMO_CAPABILITIES.shareExpiry).toEqual({ options: [7, 30, 90], default: 30 });
  });
});
