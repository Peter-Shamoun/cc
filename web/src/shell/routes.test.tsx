import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { routeFromLocation, signedOutUrl } from './routes';
import { NotFoundPage, SignedOutPage } from './StatusPage';
import { Sidebar, type SidebarProps } from '../components/Sidebar';
import { cookieSession } from '../auth/session';

afterEach(cleanup);

describe('path routes', () => {
  it('maps the app root, signed-out and everything else (no share pages: /shared/* is a 404)', () => {
    expect(routeFromLocation('/', '')).toEqual({ kind: 'app' });
    expect(routeFromLocation('/index.html', '')).toEqual({ kind: 'app' });
    expect(routeFromLocation('/shared/sh_abc', '')).toEqual({ kind: 'not-found' });
    expect(routeFromLocation('/share/sh_abc', '')).toEqual({ kind: 'not-found' });
    expect(routeFromLocation('/signed-out', '')).toEqual({ kind: 'signed-out', expired: false });
    expect(routeFromLocation('/signed-out', '?reason=expired')).toEqual({ kind: 'signed-out', expired: true });
    expect(routeFromLocation('/nope', '')).toEqual({ kind: 'not-found' });
    expect(routeFromLocation('/c/abc', '')).toEqual({ kind: 'not-found' });
    expect(routeFromLocation('/shared/', '')).toEqual({ kind: 'not-found' });
  });
  it('honours a sub-path base', () => {
    expect(routeFromLocation('/app/', '', '/app/')).toEqual({ kind: 'app' });
    expect(routeFromLocation('/app', '', '/app/')).toEqual({ kind: 'app' });
    expect(routeFromLocation('/app/signed-out', '', '/app/')).toEqual({ kind: 'signed-out', expired: false });
    expect(routeFromLocation('/', '', '/app/')).toEqual({ kind: 'not-found' });
    expect(signedOutUrl('/app')).toBe('/app/signed-out');
    expect(signedOutUrl('/', true)).toBe('/signed-out?reason=expired');
  });
});

describe('status pages', () => {
  it('404 is one plain heading and a way home, and sets the tab title', () => {
    render(<NotFoundPage />);
    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Go to CC' })).toHaveAttribute('href', '/');
    expect(document.title).toBe('Page not found · CC');
  });
  it('signed-out and expired variants link to sign in', () => {
    render(<SignedOutPage expired={false} signInUrl="/signin" />);
    expect(screen.getByRole('heading', { name: 'You’re signed out' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign in again' })).toHaveAttribute('href', '/signin');
    cleanup();
    render(<SignedOutPage expired />);
    expect(screen.getByRole('heading', { name: 'Your session expired' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Sign in again' })).toHaveAttribute('href', '/');
  });
});

describe('sign out', () => {
  const base = { threads: [], activeId: null, storeMode: 'server', onSelect: vi.fn(), onNew: vi.fn(), onDelete: vi.fn(), onRename: vi.fn(), onCollapse: vi.fn(), onSearch: vi.fn(), onSettings: vi.fn(), onShortcuts: vi.fn() };
  it('the account menu offers Sign out only when there is a session', () => {
    render(<Sidebar {...(base as unknown as SidebarProps)} />);
    fireEvent.click(screen.getByTestId('account-button'));
    expect(screen.queryByRole('menuitem', { name: /Sign out/ })).toBeNull();
    cleanup();
    const onSignOut = vi.fn();
    render(<Sidebar {...(base as unknown as SidebarProps)} onSignOut={onSignOut} />);
    fireEvent.click(screen.getByTestId('account-button'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Sign out/ }));
    expect(onSignOut).toHaveBeenCalledTimes(1);
  });
  it('an expired cookie session with no sign-in page lands on the expired page', () => {
    const nav = vi.fn();
    const s = cookieSession(undefined, nav);
    s.onExpired();
    s.onExpired();
    expect(nav).toHaveBeenCalledTimes(1);
    expect(nav).toHaveBeenCalledWith('/signed-out?reason=expired');
  });
});
