import { describe, expect, it, vi } from 'vitest';
import { asSession, cookieSession, CSRF_HEADER, demoTokenSession, noSession, sessionFromEnv, signOut } from './session';
import { ConversationsApi } from '../adapters/conversationsApi';
import { HttpAnalystAdapter } from '../adapters/http';

function fakeFetch(status = 200, body: unknown = { items: [] }) {
  const calls: { url: string; init: RequestInit }[] = [];
  const impl = vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
  }) as unknown as typeof fetch;
  return { calls, impl };
}

describe('sessionFromEnv', () => {
  it('picks cookie, demo token or none', () => {
    expect(sessionFromEnv({ VITE_AUTH: 'cookie' }).kind).toBe('cookie');
    expect(sessionFromEnv({ VITE_ANALYST_DEMO_TOKEN: 'demo:acme_labs:ana@xifin.example' }).kind).toBe('demo-token');
    expect(sessionFromEnv({}).kind).toBe('none');
  });
  it('refuses a non-demo token instead of shipping it in the bundle', () => {
    expect(() => sessionFromEnv({ VITE_ANALYST_DEMO_TOKEN: 'eyJhbGciOiJSUzI1NiJ9.real.jwt' })).toThrow(/demo token/);
  });
});

describe('sessions', () => {
  it('demo token -> bearer header; cookie -> CSRF header and no Authorization', () => {
    expect(demoTokenSession('demo:a_b:u').headers()).toEqual({ authorization: 'Bearer demo:a_b:u' });
    expect(cookieSession().headers()).toEqual({ [CSRF_HEADER]: '1' });
    expect(noSession.headers()).toEqual({});
    expect(asSession('demo:a_b:u').kind).toBe('demo-token');
    expect(asSession(undefined)).toBe(noSession);
  });
  it('cookie onExpired navigates to sign-in once, with only the pathname as next', () => {
    const nav = vi.fn();
    window.history.replaceState(null, '', '/c/abc?q=secret#frag');
    const s = cookieSession('/signin', nav);
    s.onExpired();
    s.onExpired();
    expect(nav).toHaveBeenCalledTimes(1);
    expect(nav).toHaveBeenCalledWith('/signin?next=%2Fc%2Fabc');
    window.history.replaceState(null, '', '/');
  });
});

describe('adapters use the session', () => {
  it('ConversationsApi sends session headers and reports 401', async () => {
    const f = fakeFetch(401, { error: { code: 'auth_expired', message: 'x' } });
    const onExpired = vi.fn();
    const api = new ConversationsApi('', { kind: 'cookie', headers: () => ({ [CSRF_HEADER]: '1' }), onExpired }, f.impl);
    await expect(api.list()).rejects.toThrow();
    const h = f.calls[0].init.headers as Record<string, string>;
    expect(h[CSRF_HEADER]).toBe('1');
    expect(h.authorization).toBeUndefined();
    expect(f.calls[0].url).not.toMatch(/token|session/i);
    expect(onExpired).toHaveBeenCalledTimes(1);
  });
  it('HttpAnalystAdapter reports a 401 before the answer starts', async () => {
    const onExpired = vi.fn();
    const spy = vi.spyOn(globalThis, 'fetch').mockResolvedValue(new Response(JSON.stringify({ error: { code: 'auth_expired', message: 'x' } }), { status: 401 }));
    const a = new HttpAnalystAdapter('', { kind: 'cookie', headers: () => ({ [CSRF_HEADER]: '1' }), onExpired }, { maxRetries: 0 });
    const out: unknown[] = [];
    for await (const e of a.streamTurn({ conversationId: 'c', signal: new AbortController().signal, messages: [{ id: 'u', role: 'user', text: 'q' }] } as never)) out.push(e);
    expect(onExpired).toHaveBeenCalledTimes(1);
    const init = spy.mock.calls[0][1] as RequestInit;
    expect((init.headers as Record<string, string>)[CSRF_HEADER]).toBe('1');
    expect(init.credentials).toBe('same-origin');
    spy.mockRestore();
  });
});

describe('signOut', () => {
  it('POSTs with the session headers (CSRF for cookie), then navigates to sign-in', async () => {
    const calls: { url: string; init: RequestInit }[] = [];
    const fetchImpl = vi.fn(async (url: string, init: RequestInit) => { calls.push({ url, init }); return new Response(null, { status: 204 }); }) as unknown as typeof fetch;
    const nav = vi.fn();
    expect(await signOut('', cookieSession('/signin'), { signInUrl: '/signin', fetchImpl, nav })).toBe(true);
    expect(calls[0].url).toBe('/api/auth/sign-out');
    expect(calls[0].init).toMatchObject({ method: 'POST', credentials: 'same-origin', cache: 'no-store', headers: { [CSRF_HEADER]: '1' } });
    expect(nav).toHaveBeenCalledWith('/signin');
  });
  it('still leaves when the request fails, and never puts anything in the URL', async () => {
    const nav = vi.fn();
    const fetchImpl = vi.fn(async () => { throw new TypeError('offline'); }) as unknown as typeof fetch;
    expect(await signOut('', demoTokenSession('demo:a_b:u'), { fetchImpl, nav })).toBe(false);
    expect(nav).toHaveBeenCalledWith(window.location.pathname);
  });
});
