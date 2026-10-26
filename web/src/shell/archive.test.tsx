import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from '../App';

vi.mock('../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

async function ask(text: string) {
  const box = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(box, { target: { value: text } });
  fireEvent.keyDown(box, { key: 'Enter' });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull());
}
const rows = () => within(screen.getByTestId('sidebar')).queryAllByTestId('thread-item');

describe('archive (ChatGPT-style)', () => {
  it('archives from the chat menu, offers Undo, and restores from Settings → Data controls', async () => {
    render(<App />);
    await ask('Total paid by payer');
    await waitFor(() => expect(rows()).toHaveLength(1));
    fireEvent.click(within(rows()[0]).getByRole('button', { name: /^Options for / }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }));
    await waitFor(() => expect(rows()).toHaveLength(0));
    // The open chat was archived: we're back on a fresh chat.
    expect(screen.queryAllByTestId('msg-user')).toHaveLength(0);
    expect(await screen.findByText(/Archived “Total paid by payer/)).toBeInTheDocument();
    const undo = await screen.findByRole('button', { name: 'Undo' });
    await act(async () => { fireEvent.click(undo); });
    await waitFor(() => expect(rows()).toHaveLength(1));

    // Archive again, then restore from Settings.
    fireEvent.click(within(rows()[0]).getByRole('button', { name: /^Options for / }));
    fireEvent.click(await screen.findByRole('menuitem', { name: 'Archive' }));
    await waitFor(() => expect(rows()).toHaveLength(0));
    fireEvent.keyDown(window, { key: ',', ctrlKey: true });
    const dlg = await screen.findByRole('dialog', { name: 'Settings' });
    fireEvent.click(within(dlg).getByRole('tab', { name: 'Data controls' }));
    expect(within(dlg).getByText(/1 archived/)).toBeInTheDocument();
    fireEvent.click(within(dlg).getByTestId('archived-manage'));
    const list = within(dlg).getByRole('list', { name: 'Archived chats' });
    fireEvent.click(within(list).getByRole('button', { name: /^Unarchive / }));
    await waitFor(() => expect(rows()).toHaveLength(1));
    expect(within(dlg).getByTestId('archived-manage')).toBeDisabled();
  });
});
