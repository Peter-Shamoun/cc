/**
 * PHI safety (BRIEF / CONTRACT §10): row data must never reach the console, web storage, cookies, the URL,
 * history state, document.title or a request URL. Every fixture (including the claim-line sample with a
 * PHI member-id column) is streamed through the real App; then each sink is searched for row values.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from '../App';
import { FIXTURES } from '../fixtures';
import { ANSWER_FIXTURES } from '../fixtures/answer';
import { V1_FIXTURES } from '../fixtures/v1';

vi.mock('../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

type Fx = { id: string; prompt: string; steps: unknown[] };
const ALL = [...FIXTURES, ...ANSWER_FIXTURES, ...(V1_FIXTURES as unknown as Fx[])] as Fx[];

/** Every table cell in a fixture (walks nested steps so v1 outcome shapes are covered too). */
function rowValues(fx: Fx): { values: string[]; phi: string[] } {
  const values = new Set<string>(); const phi = new Set<string>();
  const walk = (n: unknown) => {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    const o = n as { type?: string; rows?: unknown[][]; columns?: { phi?: boolean }[] };
    if (o.type === 'table' && Array.isArray(o.rows)) {
      for (const r of o.rows) r.forEach((v, j) => {
        const s = String(v ?? '');
        // Distinctive values only: short codes/numbers can occur by coincidence in unrelated text.
        if (s.length >= 6 && !fx.prompt.includes(s)) { values.add(s); if (o.columns?.[j]?.phi) phi.add(s); }
      });
    }
    Object.values(o).forEach(walk);
  };
  walk(fx.steps);
  return { values: [...values], phi: [...phi] };
}

const sinks: string[] = [];
const METHODS = ['log', 'info', 'warn', 'error', 'debug', 'trace', 'table', 'dir'] as const;
const realFetch = globalThis.fetch;

beforeEach(() => {
  sinks.length = 0;
  localStorage.clear(); sessionStorage.clear();
  for (const m of METHODS) vi.spyOn(console, m).mockImplementation((...a: unknown[]) => { sinks.push(`console.${m}: ${a.map(String).join(' ')}`); });
  for (const fn of ['pushState', 'replaceState'] as const) {
    const orig = history[fn].bind(history);
    vi.spyOn(history, fn).mockImplementation((state: unknown, title: string, url?: string | URL | null) => {
      sinks.push(`history.${fn}: ${JSON.stringify(state)} ${title} ${String(url ?? '')}`); orig(state, title, url);
    });
  }
  globalThis.fetch = vi.fn(async (input: RequestInfo | URL) => {
    sinks.push(`fetch: ${typeof input === 'string' ? input : input instanceof URL ? input.href : input.url}`);
    return new Response('{}', { status: 404 });
  }) as typeof fetch;
  URL.createObjectURL = vi.fn(() => 'blob:x');
  URL.revokeObjectURL = vi.fn();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); globalThis.fetch = realFetch; });

function snapshotSinks(): string {
  return [
    ...sinks,
    `localStorage: ${JSON.stringify({ ...localStorage })}`,
    `sessionStorage: ${JSON.stringify({ ...sessionStorage })}`,
    `cookie: ${document.cookie}`,
    `url: ${location.href}`,
    `title: ${document.title}`,
    `window.name: ${window.name}`,
  ].join('\n');
}

describe('PHI never leaves the rendered page', () => {
  const withRows = ALL.filter((f) => rowValues(f).values.length);

  it('has fixtures with table rows (and at least one PHI column) to check', () => {
    expect(withRows.length).toBeGreaterThan(1);
    expect(ALL.some((f) => rowValues(f).phi.length)).toBe(true);
  });

  it.each(withRows.map((f) => [f.id, f] as const))('%s: row values stay out of console, storage, URL, history, title and requests', async (_id, fx) => {
    const { values, phi } = rowValues(fx);
    render(<App />);
    const box = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(box, { target: { value: fx.prompt } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull(), { timeout: 8000 });
    await waitFor(() => expect(document.querySelector('[data-testid="data-table"]')).not.toBeNull(), { timeout: 8000 });

    const out = snapshotSinks();
    const leaked = values.filter((v) => out.includes(v));
    expect(leaked, `row values found in a sink:\n${out.slice(0, 2000)}`).toEqual([]);
    // PHI columns are masked in the DOM as well.
    for (const v of phi) expect(document.body.textContent ?? '').not.toContain(v);
    // Only preferences may be persisted.
    expect(Object.keys(localStorage).every((k) => k.startsWith('cc.pref.'))).toBe(true);
    expect(Object.keys(sessionStorage)).toEqual([]);
  });
});
