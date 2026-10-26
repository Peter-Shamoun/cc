import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from '../App';
import { TemporaryAwareStore } from './temporaryStore';

vi.mock('../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/'); });

async function ask(text: string) {
  const box = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: 'Enter' });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull());
}
const rows = () => within(screen.getByTestId('sidebar')).queryAllByTestId('thread-item');
const user = (id: string) => ({ id, role: 'user' as const, text: `Denials ${id}`, createdAt: '2026-10-25T00:00:00Z' });

describe('TemporaryAwareStore (QA-S22)', () => {
  it('keeps a conversation started in temporary mode out of the list and search, and discards it', async () => {
    const s = new TemporaryAwareStore();
    await s.appendMessage('saved', user('u1'), 'Saved chat');
    s.temporary = true;
    await s.appendMessage('tmp', user('u2'), 'Temp chat');
    await s.appendMessage('saved', user('u3'));
    expect((await s.listConversations()).items.map((t) => t.id)).toEqual(['saved']);
    expect((await s.search('Denials')).map((h) => h.id)).toEqual(['saved']);
    expect(s.isTemporary('tmp')).toBe(true);
    expect(await s.getConversation('tmp')).toBeDefined();
    await s.discardTemporary();
    expect(await s.getConversation('tmp')).toBeUndefined();
    expect(s.isTemporary('tmp')).toBe(false);
    await s.appendMessage('tmp', user('u4')); // a run ending after the discard
    expect(await s.getConversation('tmp')).toBeUndefined();
    expect(await s.getConversation('saved')).toBeDefined();
  });
});

describe('temporary chat in the shell (QA-S22)', () => {
  it('toggle on: quiet heading + note, the chat stays out of the sidebar and URL, no share dialog; toggle off clears it', async () => {
    render(<App />);
    const toggle = screen.getByTestId('temporary-toggle');
    expect(toggle).toHaveAttribute('aria-pressed', 'false');
    fireEvent.click(toggle);
    expect(screen.getByTestId('temporary-toggle')).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('Temporary chat');
    expect(screen.getByTestId('temporary-note').textContent).toMatch(/won’t appear in your history/);
    await ask('Total paid by payer');
    expect(screen.getAllByTestId('msg-user')).toHaveLength(1);
    expect(rows()).toHaveLength(0);
    expect(window.location.hash).toBe('');
    expect(screen.getByTestId('share-button')).toHaveAttribute('aria-haspopup', 'menu');
    // Still shown on a temp chat with messages, so it can be left.
    fireEvent.click(screen.getByTestId('temporary-toggle'));
    await waitFor(() => expect(screen.queryAllByTestId('msg-user')).toHaveLength(0));
    expect(screen.getByRole('heading', { level: 1 }).textContent).toBe('What should we look into?');
    await new Promise((r) => setTimeout(r, 50));
    expect(rows()).toHaveLength(0);
  });
  it('a normal chat with messages hides the toggle and keeps sidebar + URL; opening a saved chat leaves temporary mode', async () => {
    render(<App />);
    await ask('Total paid by payer');
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(screen.queryByTestId('temporary-toggle')).toBeNull();
    expect(window.location.hash).toMatch(/^#\/c\//);
    fireEvent.click(within(screen.getByTestId('sidebar')).getByText('New chat').closest('button')!);
    fireEvent.click(await screen.findByTestId('temporary-toggle'));
    await ask('Top denial reasons');
    expect(rows()).toHaveLength(1);
    fireEvent.click(rows()[0].querySelector<HTMLElement>('[data-thread-link]')!);
    await waitFor(() => expect(screen.getByText('Total paid by payer')).toBeInTheDocument());
    expect(screen.queryByTestId('temporary-toggle')).toBeNull();
  });
});
