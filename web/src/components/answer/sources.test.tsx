import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { SourcesChip, sourceRows } from './Sources';
import type { Part, Source } from '../../api/contract';

afterEach(cleanup);

const table = {
  type: 'table', id: 'p_t', title: 'Denial rate by payer', rowCount: 2,
  columns: [{ key: 'payer', label: 'Payer', type: 'string' }, { key: 'rate', label: 'Denial rate', type: 'percent' }],
  rows: [['SYNTH-PAYER-ALPHA', 0.123], ['SYNTH-PAYER-BETA', 0.2]], source: { views: ['sem.claims'] },
} as unknown as Part;
const sources: Source[] = [
  { id: 's2', kind: 'view', name: 'sem.providers', label: 'Providers', role: 'explored' },
  { id: 's1', kind: 'view', name: 'sem.claims', label: 'Claims', role: 'answer', description: 'One row per claim.', partIds: ['p_t'] },
  { id: 's3', kind: 'glossary', name: 'denial_rate', label: 'Denial rate def', role: 'answer' },
] as Source[];

describe('data-source citations (contract 1.8.0 §14)', () => {
  it('orders answer sources first and takes columns from linked table labels only', () => {
    const rows = sourceRows(sources, [table]);
    expect(rows.map((r) => r.id)).toEqual(['s1', 's3', 's2']);
    expect(rows[0].columns).toEqual(['Payer', 'Denial rate']);
    expect(rows[1].columns).toEqual([]);
  });
  it('falls back to table source views without server sources', () => {
    expect(sourceRows(undefined, [table]).map((r) => [r.name, r.role, r.columns.length])).toEqual([['sem.claims', 'answer', 2]]);
    expect(sourceRows(undefined, [])).toEqual([]);
  });
  it('chip opens a popover listing tables and columns, never row values; Escape closes', () => {
    render(<SourcesChip sources={sources} parts={[table]} />);
    const chip = screen.getByTestId('sources-chip');
    expect(chip).toHaveTextContent('Sources');
    expect(chip).toHaveTextContent('sem.claims');
    expect(chip).toHaveTextContent('+1');
    fireEvent.click(chip);
    const pop = screen.getByTestId('sources-popover');
    expect(within(pop).getByText('Claims')).toBeTruthy();
    expect(within(pop).getByText('sem.claims')).toBeTruthy();
    expect(within(pop).getByText('Payer')).toBeTruthy();
    expect(within(pop).getByText(/Also looked at \(1\)/)).toBeTruthy();
    expect(pop.textContent).not.toMatch(/SYNTH-PAYER|0\.123|12\.3/);
    // unknown kind renders as a plain chip
    expect(pop.querySelector('[data-kind="glossary"]')).toBeTruthy();
    fireEvent.keyDown(pop, { key: 'Escape' });
    expect(screen.queryByTestId('sources-popover')).toBeNull();
  });
  it('renders nothing when there are no sources', () => {
    const { container } = render(<SourcesChip sources={[]} parts={[]} />);
    expect(container.innerHTML).toBe('');
  });
});
