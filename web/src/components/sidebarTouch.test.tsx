import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { Sidebar, type SidebarProps } from './Sidebar';
import type { ThreadSummary } from '../adapters/types';

afterEach(cleanup);
const now = new Date().toISOString();
const threads: ThreadSummary[] = ['Denial rate by payer', 'Top providers Q3', 'Paid amount trend'].map((title, i) => ({ id: `c${i}`, title, updatedAt: now }));
function setup() {
  const props = {
    threads, activeId: 'c1', storeMode: 'local', onSelect: vi.fn(), onNew: vi.fn(), onDelete: vi.fn(), onRename: vi.fn(),
    onCollapse: vi.fn(), onSearch: vi.fn(), onSettings: vi.fn(), onShortcuts: vi.fn(),
  } as unknown as SidebarProps;
  render(<Sidebar {...props} />);
  return props;
}

// QA-S38: on touch the drawer rows are plain titles; long-press (contextmenu) opens a row's options.
describe('sidebar rows on touch', () => {
  it('only the open chat keeps a visible "…" on small screens; other rows ignore taps on the hidden button', () => {
    setup();
    const opts = screen.getAllByTestId('thread-options');
    expect(opts).toHaveLength(3);
    expect(opts[1].className).toMatch(/max-md:opacity-100/);
    for (const i of [0, 2]) {
      expect(opts[i].className).not.toMatch(/max-md:opacity-100/);
      expect(opts[i].className).toMatch(/\[@media\(hover:none\)\]:pointer-events-none/);
    }
  });
  it('a long-press / right-click on a row opens its options menu anchored to that row', () => {
    const p = setup();
    const row = screen.getByRole('button', { name: 'Paid amount trend' });
    expect(fireEvent.contextMenu(row)).toBe(false); // default (browser menu / text selection) prevented
    expect(screen.getByRole('menu', { name: 'Chat options' })).toBeInTheDocument();
    expect(screen.getAllByTestId('thread-options')[2]).toHaveAttribute('aria-expanded', 'true');
    expect(p.onSelect).not.toHaveBeenCalled();
  });
});
