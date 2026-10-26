import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { ChartPart } from '../../lib/contract';

// Chart "Copy as image" and click-a-bar → "Ask about …" (ChatGPT-style data follow-up). Vega is stubbed: the view
// records its click listener so a test can "click a bar" with a datum, and toImageURL returns a tiny PNG data URL.
const h = vi.hoisted(() => ({ click: null as null | ((e: unknown, item?: unknown) => void) }));
vi.mock('vega-embed', () => ({
  default: vi.fn(async () => ({ view: {
    finalize() {},
    toImageURL: async () => 'data:image/png;base64,iVBORw0KGgo=',
    addEventListener: (t: string, f: (e: unknown, item?: unknown) => void) => { if (t === 'click') h.click = f; },
  } })),
}));
vi.mock('vega', () => ({ loader: () => ({}) }));

const bar = { type: 'chart', id: 'c1', title: 'Denial rate by payer', alt: 'bar chart',
  spec: { mark: 'bar', encoding: { x: { field: 'payer', type: 'nominal' }, y: { field: 'rate', type: 'quantitative' } } },
  data: { values: [{ payer: 'Cascade Health', rate: 0.19 }, { payer: 'Summit', rate: 0.07 }] } } as unknown as ChartPart;

const write = vi.fn(async () => {});
beforeEach(() => {
  h.click = null;
  write.mockClear();
  vi.stubGlobal('ClipboardItem', class { constructor(public items: Record<string, Blob>) {} });
  Object.defineProperty(navigator, 'clipboard', { value: { write, writeText: vi.fn() }, configurable: true });
  // fetch() of a data: URL is blocked by the CSP in the browser: fail the test if the copy path uses it
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('fetch is blocked by connect-src'); }));
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

const mount = async (props: Record<string, unknown> = {}, part = bar) => {
  const { ChartView } = await import('./ChartView');
  render(<ChartView part={part} {...props} />);
  await waitFor(() => expect(screen.getByTestId('chart').querySelector('[aria-busy="false"]')).toBeTruthy());
};

describe('chart: Copy as image', () => {
  it('writes a PNG ClipboardItem and flips the icon to a check', async () => {
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Copy as image' }));
    await waitFor(() => expect(write).toHaveBeenCalledTimes(1));
    const item = (write.mock.calls[0] as unknown as [{ items: Record<string, Blob> }[]])[0][0];
    expect(Object.keys(item.items)).toEqual(['image/png']);
    await waitFor(() => expect(screen.getByRole('button', { name: 'Copied' }).querySelector('.lucide-check')).toBeTruthy());
  });
  it('a refused clipboard fails quietly (no error text, button stays)', async () => {
    write.mockRejectedValueOnce(new Error('NotAllowedError'));
    await mount();
    fireEvent.click(screen.getByRole('button', { name: 'Copy as image' }));
    await waitFor(() => expect(write).toHaveBeenCalled());
    expect(screen.getByRole('button', { name: 'Copy as image' })).toBeTruthy();
    expect(screen.queryByText(/NotAllowed|failed/i)).toBeNull();
  });
  it('not offered where the browser has no image clipboard', async () => {
    vi.stubGlobal('ClipboardItem', undefined);
    await mount();
    expect(screen.queryByRole('button', { name: 'Copy as image' })).toBeNull();
    expect(screen.getByRole('button', { name: 'Download PNG' })).toBeTruthy();
  });
});

describe('chart: click a bar to ask about it', () => {
  it('clicking a bar offers one quiet "Ask about …" row; clicking it sends a follow-up and closes', async () => {
    const ask = vi.fn();
    await mount({ onAsk: ask });
    act(() => h.click!({}, { datum: { payer: 'Cascade Health', rate: 0.19 } }));
    const row = screen.getByTestId('chart-ask');
    expect(row.textContent).toContain('Ask about Cascade Health');
    fireEvent.click(screen.getByRole('button', { name: /Ask about Cascade Health/ }));
    expect(ask).toHaveBeenCalledWith('Tell me more about Cascade Health in "Denial rate by payer"');
    expect(screen.queryByTestId('chart-ask')).toBeNull();
  });
  it('Dismiss closes it; clicking empty space or a measure-only datum offers nothing', async () => {
    const ask = vi.fn();
    await mount({ onAsk: ask });
    act(() => h.click!({}, { datum: { payer: 'Summit', rate: 0.07 } }));
    fireEvent.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(screen.queryByTestId('chart-ask')).toBeNull();
    act(() => h.click!({}, null));
    expect(screen.queryByTestId('chart-ask')).toBeNull();
    act(() => h.click!({}, { datum: { rate: 0.07 } }));
    expect(screen.queryByTestId('chart-ask')).toBeNull();
    expect(ask).not.toHaveBeenCalled();
  });
  it('without onAsk (artifact panel, streaming) a click does nothing', async () => {
    await mount();
    act(() => h.click!({}, { datum: { payer: 'Summit', rate: 0.07 } }));
    expect(screen.queryByTestId('chart-ask')).toBeNull();
  });
});

describe('datumLabel', () => {
  it('prefers nominal/ordinal x/y/color, falls back to temporal (as a date), never a measure; caps at 60 chars', async () => {
    const { datumLabel } = await import('./ChartView');
    const enc = (e: Record<string, unknown>) => ({ encoding: e });
    expect(datumLabel(enc({ x: { field: 'r', type: 'quantitative' }, y: { field: 'p', type: 'nominal' } }), { r: 1, p: 'Summit' })).toBe('Summit');
    expect(datumLabel(enc({ x: { field: 'm', type: 'temporal' }, color: { field: 's', type: 'nominal' } }), { m: new Date('2026-03-01T00:00:00Z'), s: 'Cascade' })).toBe('Cascade');
    expect(datumLabel(enc({ x: { field: 'm', type: 'temporal' } }), { m: new Date('2026-03-01T00:00:00Z') })).toBe('2026-03-01');
    expect(datumLabel(enc({ y: { field: 'v', type: 'quantitative' } }), { v: 3 })).toBeNull();
    expect(datumLabel(enc({ x: { field: 'p', type: 'nominal' } }), { p: '   ' })).toBeNull();
    expect(datumLabel({}, { p: 'x' })).toBeNull();
    expect(datumLabel({ layer: [{ mark: 'bar', encoding: { x: { field: 'p', type: 'nominal' } } }, { mark: 'rule' }] }, { p: 'Layered' })).toBe('Layered');
    expect(datumLabel(enc({ x: { field: 'p', type: 'nominal' } }), 'nope')).toBeNull();
    expect(datumLabel(enc({ x: { field: 'p', type: 'ordinal' } }), { p: 'y'.repeat(80) })).toHaveLength(60);
  });
});

describe('dataUrlBlob', () => {
  it('decodes a base64 PNG data URL without fetch (the CSP blocks fetch of data: URLs)', async () => {
    const { dataUrlBlob } = await import('./ChartView');
    const blob = dataUrlBlob('data:image/png;base64,iVBORw0KGgo=');
    expect(fetch).not.toHaveBeenCalled();
    expect(blob.type).toBe('image/png');
    expect(Array.from(new Uint8Array(await blob.arrayBuffer()))).toEqual([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  });

  it('refuses anything but a base64 data URL', async () => {
    const { dataUrlBlob } = await import('./ChartView');
    expect(() => dataUrlBlob('https://example.com/x.png')).toThrow();
  });
});
