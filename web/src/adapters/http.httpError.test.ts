import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { HTTP_ERROR_COPY, httpError } from './http';

const j = (status: number, error: Record<string, unknown> | null, headers: Record<string, string> = {}) =>
  new Response(JSON.stringify(error === null ? {} : { error: { message: '', ...error } }), { status, headers: { 'content-type': 'application/json', ...headers } });

describe('httpError: error.code first, then legacyCode, then status (contract 1.5)', () => {
  it('404 not_found is "Not found", not access_denied', async () => {
    expect(await httpError(j(404, { code: 'not_found' }))).toMatchObject({ code: 'not_found', title: 'Not found', retryable: false });
  });

  it('404 access_denied (unknown-or-forbidden, no enumeration) stays access_denied', async () => {
    expect(await httpError(j(404, { code: 'access_denied' }))).toMatchObject({ code: 'access_denied', title: 'Not available', retryable: false });
  });

  it('413 payload_too_large: "Message too long", not retryable, limit from maxBytes', async () => {
    const p = await httpError(j(413, { code: 'payload_too_large', legacyCode: 'validation', maxBytes: 65536 }));
    expect(p).toMatchObject({ code: 'payload_too_large', title: 'Message too long', retryable: false });
    expect(p.message).toContain('64 KB');
  });

  it('413 from a proxy (HTML body, no code) is still "Message too long"', async () => {
    expect(await httpError(new Response('<html>413 Request Entity Too Large</html>', { status: 413 }))).toMatchObject({ code: 'payload_too_large', retryable: false });
  });

  it('409 clarification_expired and a bare 409 ask the user to ask again, not retryable', async () => {
    expect(await httpError(j(409, { code: 'clarification_expired' }))).toMatchObject({ code: 'clarification_expired', title: 'Ask again', retryable: false });
    expect(await httpError(new Response('', { status: 409 }))).toMatchObject({ code: 'conflict', title: 'Ask again', retryable: false });
  });

  it('rate_limited carries Retry-After; a 503 with rate_limited code is still rate_limited', async () => {
    expect(await httpError(j(429, { code: 'rate_limited' }, { 'retry-after': '7' }))).toMatchObject({ code: 'rate_limited', retryable: true, retryAfterMs: 7000 });
    expect(await httpError(j(503, { code: 'rate_limited' }))).toMatchObject({ code: 'rate_limited', retryable: true });
  });

  it('Retry-After is ignored for non-rate-limit errors', async () => {
    expect(await httpError(j(503, { code: 'internal' }, { 'retry-after': '5' }))).not.toHaveProperty('retryAfterMs');
  });

  it('code wins over status: 400 + auth_expired, 403 + transcripts_unavailable', async () => {
    expect(await httpError(j(400, { code: 'auth_expired' }))).toMatchObject({ code: 'auth_expired', retryable: false });
    expect(await httpError(j(403, { code: 'transcripts_unavailable' }))).toMatchObject({ code: 'transcripts_unavailable', retryable: false });
  });

  it('unknown code falls back to legacyCode, then status', async () => {
    expect(await httpError(j(400, { code: 'brand_new_code', legacyCode: 'validation' }))).toMatchObject({ code: 'validation', retryable: false });
    expect(await httpError(j(401, { code: 'brand_new_code' }))).toMatchObject({ code: 'auth_expired' });
    expect(await httpError(j(404, { code: 'brand_new_code' }))).toMatchObject({ code: 'not_found' });
  });

  it('unknown 4xx with unknown code: not retryable, keeps the code for diagnostics', async () => {
    expect(await httpError(j(418, { code: 'http_418' }))).toMatchObject({ code: 'http_418', title: 'Request not accepted', retryable: false });
    expect(await httpError(new Response('nope', { status: 451 }))).toMatchObject({ code: 'http_451', retryable: false });
  });

  it('5xx is retryable generic copy, and never echoes a server message', async () => {
    const p = await httpError(j(500, { code: 'internal', message: 'Traceback: claim AL1234 for Jane Doe' }));
    expect(p).toMatchObject({ code: 'internal', title: 'Analyst unavailable', retryable: true });
    expect(p.message).not.toContain('Jane');
    expect(await httpError(new Response('<html>bad gateway</html>', { status: 502 }))).toMatchObject({ code: 'internal', retryable: true });
    expect(await httpError(j(504, { code: 'http_504' }))).toMatchObject({ code: 'http_504', title: 'Analyst unavailable', retryable: true });
  });

  it('known 4xx prefer the server message but cap its length', async () => {
    expect((await httpError(j(422, { code: 'validation', message: 'Question is empty.' }))).message).toBe('Question is empty.');
    expect((await httpError(j(422, { code: 'validation', message: 'x'.repeat(5000) }))).message.length).toBeLessThanOrEqual(300);
  });

  it('malformed bodies never throw: null, array, error as string, non-string code', async () => {
    for (const body of ['null', '[]', '{"error":"boom"}', '{"error":{"code":42,"message":{"a":1}}}', '']) {
      const p = await httpError(new Response(body, { status: 404 }));
      expect(p).toMatchObject({ type: 'error', code: 'not_found', retryable: false });
      expect(typeof p.message).toBe('string');
    }
  });

  it('every x-known ErrorCode in the contract schema has its own copy', () => {
    const schema = JSON.parse(fs.readFileSync(path.resolve(__dirname, '../../../api/contract/v1.schema.json'), 'utf8'));
    const known: string[] = schema.$defs.ErrorCode['x-known'];
    expect(known.length).toBeGreaterThan(5);
    expect(known.filter((c) => !HTTP_ERROR_COPY[c])).toEqual([]);
  });

  it('405 method_not_allowed (contract 1.13) is not retryable', async () => {
    expect(await httpError(j(405, { code: 'method_not_allowed' }))).toMatchObject({ code: 'method_not_allowed', retryable: false });
    expect(await httpError(new Response('', { status: 405 }))).toMatchObject({ code: 'method_not_allowed' });
  });
});
