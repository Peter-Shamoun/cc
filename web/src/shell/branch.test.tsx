import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from '../App';

vi.mock('../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

afterEach(() => { cleanup(); window.history.replaceState(null, '', '/'); });

async function ask(text: string) {
  const n = screen.queryAllByTestId('more-actions').length;
  const box = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: 'Enter' });
  // Done = the new answer has its action row (the mock streams on timers).
  await waitFor(() => expect(screen.queryAllByTestId('more-actions').length).toBe(n + 1), { timeout: 8000 });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull());
}

// QA-S41: ChatGPT's More menu on an answer offers "Branch in new chat".
describe('branch in new chat', () => {
  it('copies the thread up to that answer into a new chat and leaves the original alone', async () => {
    render(<App />);
    await ask('Total paid by payer');
    await ask('Denial rate by payer');
    expect(screen.getAllByTestId('msg-user')).toHaveLength(2);
    const nav = screen.getByRole('navigation', { name: 'Chats' });
    const before = within(nav).getAllByTestId('thread-item').length;
    // Branch from the FIRST answer.
    fireEvent.click(screen.getAllByTestId('more-actions')[0]);
    fireEvent.click(screen.getByRole('menuitem', { name: 'Branch in new chat' }));
    await waitFor(() => expect(within(nav).getAllByTestId('thread-item')).toHaveLength(before + 1));
    expect(within(nav).getByText(/^Branch · /)).toBeTruthy();
    await waitFor(() => expect(screen.getAllByTestId('msg-user')).toHaveLength(1));
    expect(screen.getByTestId('msg-user')).toHaveTextContent('Total paid by payer');
    expect(screen.getAllByTestId('msg-assistant')).toHaveLength(1);
    // The original still has both exchanges.
    fireEvent.click(within(nav).getAllByTestId('thread-item').map((li) => li.querySelector('button')!).find((b) => !(b.textContent ?? '').startsWith('Branch'))!);
    await waitFor(() => expect(screen.getAllByTestId('msg-user')).toHaveLength(2));
  });

  it('is not offered in a temporary chat', async () => {
    render(<App />);
    fireEvent.click(screen.getByTestId('temporary-toggle'));
    await ask('Total paid by payer');
    fireEvent.click(screen.getAllByTestId('more-actions')[0]);
    expect(screen.queryByRole('menuitem', { name: 'Branch in new chat' })).toBeNull();
  });
});
