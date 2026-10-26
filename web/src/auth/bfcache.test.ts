/** Back button after sign-out: the page left signed out is blanked, and a bfcache restore reloads it. */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cookieSession, leaveSignedOut, pageHooks, resetSignOutState, signOut } from './session';

const pageshow = (persisted: boolean) => { const e = new Event('pageshow'); Object.defineProperty(e, 'persisted', { value: persisted }); window.dispatchEvent(e); };
let reload: ReturnType<typeof vi.fn>;
const realReload = pageHooks.reload;

beforeEach(() => {
  document.body.innerHTML = '<div id="root"><main>Claims for Jane Q. Synthetic</main></div>';
  reload = vi.fn(); pageHooks.reload = reload as never;
});
afterEach(() => { resetSignOutState(); pageHooks.reload = realReload; document.body.innerHTML = ''; });

describe('bfcache after sign-out', () => {
  it('signOut blanks the app before navigating, so a bfcache snapshot holds nothing', async () => {
    let atNav = '';
    await signOut('', cookieSession('/signin', vi.fn()), { fetchImpl: async () => new Response(null, { status: 204 }), nav: () => { atNav = document.body.textContent ?? ''; }, signInUrl: '/signin' });
    expect(atNav).not.toContain('Synthetic');
    expect(document.getElementById('root')).not.toBeNull(); // the mount point stays, empty
  });

  it('a persisted pageshow after leaving signed out reloads the page', () => {
    leaveSignedOut(vi.fn(), '/signin');
    pageshow(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('a normal (non-persisted) pageshow, or one without a sign-out, does nothing', () => {
    pageshow(true);
    expect(reload).not.toHaveBeenCalled();
    leaveSignedOut(vi.fn(), '/signin');
    pageshow(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it('an expired session also leaves blanked and reload-guarded', () => {
    const nav = vi.fn();
    cookieSession('/signin', nav).onExpired();
    expect(nav).toHaveBeenCalledTimes(1);
    expect(document.body.textContent).not.toContain('Synthetic');
    pageshow(true);
    expect(reload).toHaveBeenCalledTimes(1);
  });
});
