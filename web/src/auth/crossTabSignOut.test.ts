/** Signing out in one tab makes every other tab leave, so no conversation stays on screen in a signed-out browser. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { AUTH_CHANNEL, SIGNED_OUT, cookieSession, onSignedOutElsewhere, resetSignOutState, signOut } from './session';

afterEach(() => resetSignOutState());
const tick = () => new Promise((r) => setTimeout(r, 20));

describe('cross-tab sign-out', () => {
  it('another tab signing out sends this tab to sign-in (with next=path only)', async () => {
    const nav = vi.fn();
    cookieSession('/signin', nav);
    const otherTab = new BroadcastChannel(AUTH_CHANNEL);
    otherTab.postMessage(SIGNED_OUT);
    await tick();
    otherTab.close();
    expect(nav).toHaveBeenCalledTimes(1);
    expect(nav.mock.calls[0][0]).toMatch(/^\/signin\?next=%2F/);
    expect(nav.mock.calls[0][0]).not.toContain('#');
  });

  it('without a sign-in URL the tab reloads its path, dropping what is on screen', async () => {
    const nav = vi.fn();
    cookieSession(undefined, nav);
    const otherTab = new BroadcastChannel(AUTH_CHANNEL);
    otherTab.postMessage(SIGNED_OUT);
    await tick();
    otherTab.close();
    expect(nav).toHaveBeenCalledWith(window.location.pathname);
  });

  it('signOut announces a constant (no ids or data) and its own tab ignores the echo', async () => {
    const seen: unknown[] = [];
    const otherTab = new BroadcastChannel(AUTH_CHANNEL);
    otherTab.onmessage = (e) => seen.push(e.data);
    const sessionNav = vi.fn();
    const session = cookieSession('/signin', sessionNav);
    const leave = vi.fn();
    await signOut('', session, { fetchImpl: async () => new Response(null, { status: 204 }), nav: leave, signInUrl: '/signin' });
    await tick();
    otherTab.close();
    expect(seen).toEqual([SIGNED_OUT]);
    expect(leave).toHaveBeenCalledWith('/signin');
    expect(sessionNav).not.toHaveBeenCalled();
  });

  it('ignores unrelated messages and stops listening when unsubscribed', async () => {
    const fn = vi.fn();
    const off = onSignedOutElsewhere(fn);
    const otherTab = new BroadcastChannel(AUTH_CHANNEL);
    otherTab.postMessage('hello');
    await tick();
    expect(fn).not.toHaveBeenCalled();
    off();
    otherTab.postMessage(SIGNED_OUT);
    await tick();
    otherTab.close();
    expect(fn).not.toHaveBeenCalled();
  });
});
