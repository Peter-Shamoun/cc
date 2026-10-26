import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SearchDialog } from './SearchDialog';
import type { SearchHit } from '../adapters/types';

afterEach(cleanup);
const now = '2026-10-25T12:00:00Z';

function open(hits: SearchHit[]) {
  render(<SearchDialog threads={[]} search={async () => hits} onOpen={vi.fn()} onNew={vi.fn()} onClose={vi.fn()} />);
  fireEvent.change(screen.getByRole('combobox', { name: 'Search chats' }), { target: { value: 'denial' } });
}

describe('SearchDialog hits (QA-S10)', () => {
  it('shows the matching message snippet with the match highlighted', async () => {
    open([{ id: 'c1', title: 'Payer review', updatedAt: now, messageId: 'm2', snippet: '…the denial rate for Payer A is 12%', matchStart: 5, matchLength: 6 }]);
    const snip = await screen.findByTestId('search-snippet');
    expect(snip.textContent).toBe('…the denial rate for Payer A is 12%');
    expect(snip.querySelector('mark')?.textContent).toBe('denial');
    expect(screen.getByTestId('search-title').querySelector('mark')).toBeNull();
  });
  it('shows a server snippet even without a message id (live store)', async () => {
    open([{ id: 'c1', title: 'Payer review', updatedAt: now, snippet: 'Top denial reasons were CO-197', matchStart: 4, matchLength: 6 }]);
    expect((await screen.findByTestId('search-snippet')).querySelector('mark')?.textContent).toBe('denial');
  });
  it('highlights a title hit in the title and adds no snippet line', async () => {
    open([{ id: 'c1', title: 'Q3 denial trends', updatedAt: now, snippet: 'Q3 denial trends', matchStart: 3, matchLength: 6 }]);
    const title = await screen.findByTestId('search-title');
    expect(title.querySelector('mark')?.textContent).toBe('denial');
    expect(screen.queryByTestId('search-snippet')).toBeNull();
  });
  it('renders no mark when the offsets are missing', async () => {
    open([{ id: 'c1', title: 'Payer review', updatedAt: now, snippet: 'something else', matchStart: 0, matchLength: 0 }]);
    expect((await screen.findByTestId('search-snippet')).querySelector('mark')).toBeNull();
  });
});
