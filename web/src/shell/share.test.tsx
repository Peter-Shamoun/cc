import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from '../App';
import { HttpAnalystAdapter } from '../adapters/http';

vi.mock('../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

afterEach(() => { cleanup(); vi.unstubAllGlobals(); vi.restoreAllMocks(); document.documentElement.classList.remove('dark'); });

async function chatOnce() {
  render(<App />);
  const box = screen.getByRole('textbox', { name: 'Message' });
  fireEvent.change(box, { target: { value: 'Total paid by payer' } });
  fireEvent.keyDown(box, { key: 'Enter' });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull());
}

describe('Share & export dialog', () => {
  it('previews the chat, keeps link sharing stubbed, and offers Markdown + print (no server JSON in mock mode)', async () => {
    await chatOnce();
    fireEvent.click(screen.getByTestId('share-button'));
    const dlg = screen.getByRole('dialog', { name: 'Share chat' });
    expect(within(dlg).getByTestId('share-preview')).toHaveTextContent('Total paid by payer');
    expect(within(dlg).getByTestId('share-preview')).toHaveTextContent('2 messages');
    const link = within(dlg).getByTestId('create-link');
    expect(link).toHaveAttribute('aria-disabled', 'true');
    expect(link).toHaveAccessibleDescription(/isn't available in this demo/);
    expect(within(dlg).getByRole('button', { name: 'Copy as Markdown' })).toBeInTheDocument();
    expect(within(dlg).getByRole('button', { name: 'Download Markdown' })).toBeInTheDocument();
    expect(within(dlg).queryByRole('button', { name: 'Download JSON' })).toBeNull();
    const print = vi.fn();
    vi.stubGlobal('print', print);
    vi.stubGlobal('requestAnimationFrame', (f: FrameRequestCallback) => { f(0); return 0; });
    fireEvent.click(within(dlg).getByRole('button', { name: 'Print or save as PDF' }));
    expect(print).toHaveBeenCalled();
    expect(screen.queryByRole('dialog', { name: 'Share chat' })).toBeNull();
  });

  it('printing turns dark mode off for the snapshot and back on after', async () => {
    await chatOnce();
    document.documentElement.classList.add('dark');
    act(() => { window.dispatchEvent(new Event('beforeprint')); });
    expect(document.documentElement).not.toHaveClass('dark');
    act(() => { window.dispatchEvent(new Event('afterprint')); });
    expect(document.documentElement).toHaveClass('dark');
  });

  it('the http adapter exports with auth and names the file from Content-Disposition', async () => {
    const f = vi.fn(async (_u: string, _i?: RequestInit) => new Response('{"a":1}', { status: 200, headers: { 'content-disposition': 'attachment; filename="cc_paid_by_payer.json"' } }));
    vi.stubGlobal('fetch', f);
    const out = await new HttpAnalystAdapter('', 'tok').exportConversation('c_1', 'json');
    expect(f.mock.calls[0][0]).toBe('/api/conversations/c_1/export?format=json');
    expect(new Headers(f.mock.calls[0][1]?.headers).get('authorization')).toMatch(/tok/);
    expect(out.filename).toBe('cc_paid_by_payer.json');
  });
});
