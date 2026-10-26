import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { SidebarRail } from './Sidebar';

afterEach(cleanup);
const base = () => ({ onExpand: vi.fn(), onNew: vi.fn(), onSearch: vi.fn(), onSettings: vi.fn(), onShortcuts: vi.fn() });

// QA-S40: like ChatGPT/Claude, the collapsed rail ends in the account avatar, not a settings gear.
describe('collapsed rail account', () => {
  it('ends in the avatar, which opens the account menu (no bare gear)', () => {
    const p = base();
    render(<SidebarRail {...p} onSignOut={vi.fn()} />);
    expect(screen.queryByRole('button', { name: 'Settings' })).toBeNull();
    const btn = screen.getByTestId('rail-account');
    expect(btn).toHaveAccessibleName('Account: Demo user');
    expect(btn).toHaveTextContent('DU');
    fireEvent.click(btn);
    expect(btn).toHaveAttribute('aria-expanded', 'true');
    const items = screen.getAllByRole('menuitem').map((i) => i.textContent);
    expect(items.some((t) => t?.startsWith('Settings'))).toBe(true);
    expect(items.some((t) => t?.startsWith('Keyboard shortcuts'))).toBe(true);
    expect(items).toContain('Sign out');
    fireEvent.click(screen.getByRole('menuitem', { name: /^Settings/ }));
    expect(p.onSettings).toHaveBeenCalledOnce();
  });
  it('without a session there is no Sign out in the rail menu either', () => {
    render(<SidebarRail {...base()} />);
    fireEvent.click(screen.getByTestId('rail-account'));
    expect(screen.queryByRole('menuitem', { name: 'Sign out' })).toBeNull();
  });
});
