import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { ChartPart, TablePart } from '../lib/contract';
import { _resetErrorReporting, recentErrors } from '../lib/errorReporting';
import { buildDiagnostics } from '../lib/diagnostics';

// Synthetic PHI-shaped values only.
const SECRET = 'Patient Jane Q Synthetic, MRN 4455667788';
const PHI = /Jane|Synthetic|4455667788/;
const boom = vi.hoisted(() => ({ on: false }));

// Make the chart spec builder blow up on demand: it's used by ChartView and by the panel's Copy text.
vi.mock('../render/chartSpec', async (orig) => {
  const real = await orig<typeof import('../render/chartSpec')>();
  return { ...real, buildSpec: (...a: Parameters<typeof real.buildSpec>) => { if (boom.on) throw new Error(SECRET); return real.buildSpec(...a); } };
});

const { ChartView } = await import('./parts/ChartView');
const { DataTable } = await import('./parts/DataTable');
const { ArtifactPanel } = await import('./ArtifactPanel');

const chart = { type: 'chart', id: 'c1', title: 'Paid by month', alt: 'line chart', spec: { mark: 'line' }, data: { values: [{ m: '2026-01', v: 2 }] } } as unknown as ChartPart;
const table: TablePart = { type: 'table', id: 't1', title: 'Denials by payer', columns: [{ key: 'payer', label: 'Payer', type: 'string' }], rows: [['Northwind']], rowCount: 1 } as TablePart;

let quiet: ReturnType<typeof vi.spyOn>;
beforeEach(() => {
  quiet = vi.spyOn(console, 'error').mockImplementation(() => {}); // React's dev logging of caught errors
  window.matchMedia = vi.fn().mockImplementation((q: string) => ({ matches: false, media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
});
afterEach(() => { cleanup(); quiet.mockRestore(); _resetErrorReporting(); boom.on = false; localStorage.clear(); });

describe('section error boundaries', () => {
  it('a chart that throws becomes an inline card; the rest of the answer stays', () => {
    boom.on = true;
    render(<div><p>Answer text stays</p><ChartView part={chart} /></div>);
    const card = screen.getByTestId('section-crash');
    expect(card.getAttribute('data-section')).toBe('ChartView');
    expect(card.textContent).toContain("This part couldn't be displayed.");
    expect(card.textContent).not.toMatch(PHI);
    expect(screen.getByText('Answer text stays')).toBeTruthy();
    expect(recentErrors().at(-1)?.context?.component).toBe('ChartView');
    expect(buildDiagnostics(recentErrors().at(-1)!)).not.toMatch(PHI); // what support would get pasted
  });

  it('a chart retries by itself when its part changes (e.g. the stream fixes it)', () => {
    boom.on = true;
    const { rerender } = render(<ChartView part={chart} />);
    expect(screen.queryByTestId('section-crash')).toBeTruthy();
    boom.on = false;
    rerender(<ChartView part={{ ...chart }} />);
    expect(screen.queryByTestId('section-crash')).toBeNull();
  });

  it('a table that throws becomes an inline card', () => {
    render(<DataTable part={{ ...table, columns: null } as unknown as TablePart} />);
    expect(screen.getByTestId('section-crash').getAttribute('data-section')).toBe('DataTable');
  });

  it('the artifact panel crashing shows a panel card with Close panel; the chat keeps working', async () => {
    boom.on = true;
    const onClose = vi.fn();
    render(<main><p>Chat stays</p><ArtifactPanel artifact={{ title: 'Paid by month', related: [], part: chart }} onClose={onClose} /></main>);
    const card = screen.getByTestId('section-crash');
    expect(card.getAttribute('data-section')).toBe('ArtifactPanel');
    expect(card.textContent).not.toMatch(PHI);
    expect(screen.getByText('Chat stays')).toBeTruthy();
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Close panel' })); });
    expect(onClose).toHaveBeenCalledOnce();
  });

  it('the panel retries when another artifact is opened', () => {
    boom.on = true;
    const { rerender } = render(<ArtifactPanel artifact={{ title: 'Paid by month', related: [], part: chart }} onClose={() => {}} />);
    expect(screen.getByTestId('section-crash')).toBeTruthy();
    boom.on = false;
    rerender(<ArtifactPanel artifact={{ title: 'Denials by payer', related: [], part: table }} onClose={() => {}} />);
    expect(screen.queryByTestId('section-crash')).toBeNull();
    expect(screen.getAllByText('Denials by payer').length).toBeGreaterThan(0);
  });

  it('Try again re-renders the section', async () => {
    boom.on = true;
    render(<ChartView part={chart} />);
    boom.on = false;
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Try again' })); });
    expect(screen.queryByTestId('section-crash')).toBeNull();
  });
});
