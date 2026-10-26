import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';
import App from '../App';

vi.mock('../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

afterEach(() => { cleanup(); window.history.replaceState(null, '', '/'); });

// Peter 12:15 AM: no sharing at all. Copy/export stay (title menu); nothing makes a link or a share page.
describe('no sharing', () => {
  it('a chat has no Share button or share entries; copy/export live in the title menu', async () => {
    render(<App />);
    const box = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(box, { target: { value: 'Total paid by payer' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(screen.getAllByTestId('more-actions')).toHaveLength(1), { timeout: 8000 });
    expect(screen.queryByTestId('share-button')).toBeNull();
    expect(screen.queryByRole('button', { name: /share/i })).toBeNull();
    fireEvent.click(await screen.findByRole('button', { name: /^Chat options/ }));
    const menu = screen.getByRole('menu');
    expect(menu.textContent).toContain('Copy as Markdown');
    expect(menu.textContent).toContain('Export as Markdown');
    expect(menu.textContent).not.toMatch(/share/i);
  });

  it('/shared/<id> is the normal 404 page', () => {
    window.history.replaceState(null, '', '/shared/sh_abc');
    render(<App />);
    expect(screen.getByTestId('not-found')).toBeTruthy();
    expect(screen.queryByTestId('shared-view')).toBeNull();
  });

  it('no source file under src/shell or src/components renders share UI', () => {
    const hits: string[] = [];
    const walk = (d: string) => {
      for (const f of readdirSync(d)) {
        const p = join(d, f);
        if (statSync(p).isDirectory()) { walk(p); continue; }
        if (!/\.tsx?$/.test(f) || /\.test\./.test(f)) continue;
        if (/ShareDialog|SharedView|share-button|aria-label="Share/.test(readFileSync(p, 'utf8'))) hits.push(p);
      }
    };
    for (const d of ['shell', 'components']) walk(join(__dirname, '..', d));
    expect(hits).toEqual([]);
  });
});
