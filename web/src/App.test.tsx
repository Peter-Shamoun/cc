import { describe, expect, it, vi } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import App from './App';

vi.mock('./adapters/mock', async (orig) => {
  const m = await orig<typeof import('./adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
// vega-embed and shiki need a real browser; stub them in jsdom.
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

describe('App', () => {
  it('renders the empty state, streams a fixture and shows parts', async () => {
    const log = vi.spyOn(console, 'log');
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    render(<App />);
    expect(screen.getByText('What should we look into?')).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox', { name: 'Message' }), { target: { value: 'Which CARC codes drive Cascade Medicaid denials?' } });
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Message' }), { key: 'Enter' });
    await waitFor(() => expect(screen.getByTestId('msg-actions')).toBeInTheDocument(), { timeout: 5000 });
    expect(screen.getByTestId('data-table')).toBeInTheDocument();
    expect(screen.getAllByTestId('callout').length).toBeGreaterThan(0);
    expect(screen.getByTestId('artifact-card')).toBeInTheDocument();
    // PHI column is masked in the DOM
    expect(document.body.textContent).not.toMatch(/ZZ\d{6}/);
    // nothing but prefs in storage, no row data logged
    expect(Object.keys(localStorage).every((k) => k.startsWith('cc.pref.'))).toBe(true);
    expect(log.mock.calls.flat().join(' ')).not.toMatch(/Cascade|SYN-/);
    fireEvent.click(screen.getByTestId('artifact-card'));
    expect(screen.getByTestId('artifact-panel')).toBeInTheDocument();
  });
});
