import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';

afterEach(cleanup);
import { Sidebar, filterThreads, FILTER_AT, type SidebarProps } from './Sidebar';
import type { ThreadSummary } from '../adapters/types';

const now = new Date().toISOString();
const titles = ['Denial rate by payer', 'Top providers Q3', 'Résumé of CARC 16 denials', 'Paid amount trend', 'Cascade Medicaid outliers', 'Aetna appeals backlog'];
const fillers = Array.from({ length: 14 }, (_, i) => `Synthetic filler ${i}`);
const threads: ThreadSummary[] = [...titles, ...fillers].map((title, i) => ({ id: `c${i}`, title, updatedAt: now }));

function setup(extra: Partial<SidebarProps> = {}) {
  const props = {
    threads, activeId: null, storeMode: 'local', onSelect: vi.fn(), onNew: vi.fn(), onDelete: vi.fn(), onRename: vi.fn(),
    onCollapse: vi.fn(), onSearch: vi.fn(), onSettings: vi.fn(), onShortcuts: vi.fn(), ...extra,
  } as unknown as SidebarProps;
  render(<Sidebar {...props} />);
  return props;
}

describe('sidebar inline filter', () => {
  it('matches every word, ignoring case and accents', () => {
    expect(filterThreads(threads, 'resume denials').map((t) => t.id)).toEqual(['c2']);
    expect(filterThreads(threads, '  DENIAL ').map((t) => t.id)).toEqual(['c0', 'c2']);
    expect(filterThreads(threads, '')).toBe(threads);
  });

  it('filters the list, clears with Esc, and Enter opens the first match', () => {
    const p = setup();
    const input = screen.getByRole('searchbox', { name: 'Filter chats by title' });
    fireEvent.change(input, { target: { value: 'aetna' } });
    expect(screen.getAllByTestId('thread-item')).toHaveLength(1);
    expect(screen.getByText('1 chat match')).toBeTruthy();
    fireEvent.keyDown(input, { key: 'Enter' });
    expect(p.onSelect).toHaveBeenCalledWith('c5');
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.getAllByTestId('thread-item')).toHaveLength(threads.length);
  });

  it('no title match offers a full message search', () => {
    const p = setup();
    fireEvent.change(screen.getByTestId('sidebar-filter'), { target: { value: 'zzz' } });
    expect(screen.getByTestId('filter-empty').textContent).toContain('No chat titles match');
    fireEvent.click(screen.getByRole('button', { name: 'Search messages' }));
    expect(p.onSearch).toHaveBeenCalled();
  });

  it('short lists have no filter', () => {
    setup({ threads: threads.slice(0, FILTER_AT - 1) });
    expect(screen.queryByTestId('sidebar-filter')).toBeNull();
  });

  it('QA-S39: one search entry until the history is long (a handful of chats shows only "Search chats")', () => {
    expect(FILTER_AT).toBeGreaterThanOrEqual(20);
    setup({ threads: threads.slice(0, 8) });
    expect(screen.queryByTestId('sidebar-filter')).toBeNull();
    expect(screen.getByText('Search chats')).toBeTruthy();
  });
});

describe('sidebar active row (QA-S33)', () => {
  it('marks the open chat with aria-current and its own fill, distinct from the hover fill', () => {
    setup({ activeId: 'c1' });
    const [first, active] = screen.getAllByTestId('thread-item');
    expect(active.querySelector('button')).toHaveAttribute('aria-current', 'page');
    expect(first.querySelector('button')).not.toHaveAttribute('aria-current');
    expect(active).toHaveClass('bg-active', 'font-medium');
    expect(active.className).not.toMatch(/\bhover:bg-hover\b/);
    expect(first).toHaveClass('hover:bg-hover');
    expect(first).not.toHaveClass('bg-active');
  });
});
