import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import type { TablePart } from '../../lib/contract';
import { DataTable } from './DataTable';

afterEach(cleanup);
const base = (rows: unknown[][], extra: Partial<TablePart> = {}): TablePart => ({
  id: 't1', type: 'table', title: 'Paid', rowCount: rows.length,
  columns: [{ key: 'payer', label: 'Payer', type: 'string' }, { key: 'paid', label: 'Paid', type: 'currency' }, { key: 'rate', label: 'Rate', type: 'percent' }],
  rows, ...extra,
} as TablePart);
const bodyText = () => Array.from(document.querySelectorAll('tbody tr')).map((r) => r.textContent);

describe('DataTable edge cases', () => {
  it('0 rows reads "Query returned 0 rows"', () => {
    render(<DataTable part={base([])} full />);
    expect(screen.getByTestId('table-empty').textContent).toBe('Query returned 0 rows');
  });

  it('a filter with no match says so and offers Clear filter, not "0 rows"', () => {
    render(<DataTable part={base([['PAYER-A', 10, 0.1], ['PAYER-B', 20, 0.2]])} full />);
    fireEvent.change(screen.getByRole('textbox', { name: 'Filter rows' }), { target: { value: 'zzz' } });
    const empty = screen.getByTestId('table-empty');
    expect(empty.textContent).toMatch(/No rows match “zzz”/);
    fireEvent.click(within(empty).getByRole('button', { name: 'Clear filter' }));
    expect(bodyText()).toHaveLength(2);
  });

  it('filter matches the formatted value as shown ($1,234.50, 12.3%)', () => {
    render(<DataTable part={base([['PAYER-A', 1234.5, 0.123], ['PAYER-B', 99, 0.5]])} full />);
    const box = screen.getByRole('textbox', { name: 'Filter rows' });
    fireEvent.change(box, { target: { value: '$1,234' } });
    expect(bodyText()).toHaveLength(1);
    fireEvent.change(box, { target: { value: '12.3%' } });
    expect(bodyText()[0]).toContain('PAYER-A');
  });

  it('numeric strings, NaN and nulls render and sort sensibly', () => {
    render(<DataTable part={base([['A', '300.5', '0.1'], ['B', null, Number.NaN], ['C', '-20', 0.05], ['D', 1e9, 0.0001]])} full />);
    const text = bodyText().join('|');
    expect(text).toContain('$300.50');
    expect(text).toContain('-$20.00');
    expect(text).toContain('$1,000,000,000.00');
    expect(text).toContain('<0.1%');
    expect(text).not.toMatch(/NaN|undefined|object Object/);
    fireEvent.click(screen.getByRole('button', { name: /Paid/ }));
    expect(bodyText().map((t) => t?.[0])).toEqual(['C', 'A', 'D', 'B']);
  });

  it('unicode, emoji and RTL column names and values render intact', () => {
    const part = { ...base([['تأمين', 5, 0.5]]), columns: [{ key: 'ü', label: 'Zahlungsträger 支払者 💳', type: 'string' }, { key: 'n', label: 'עמודה', type: 'currency' }, { key: 'r', label: 'Taux (%)', type: 'percent' }] } as TablePart;
    render(<DataTable part={part} full />);
    expect(screen.getByRole('columnheader', { name: /Zahlungsträger 支払者 💳/ })).toBeTruthy();
    expect(screen.getByRole('columnheader', { name: /עמודה/ })).toBeTruthy();
    expect(bodyText()[0]).toContain('تأمين');
  });

  it('a 1M-row result shows its preview and the true count', () => {
    const rows = Array.from({ length: 200 }, (_, i) => [`P${i}`, i, 0.01]);
    render(<DataTable part={base(rows, { rowCount: 1_000_000 })} />);
    expect(document.body.textContent).toMatch(/1,000,000/);
  });

  it('rows shorter or longer than the columns do not crash', () => {
    render(<DataTable part={base([['A'], ['B', 1, 0.1, 'extra'], [] as unknown[]])} full />);
    expect(bodyText()).toHaveLength(3);
    expect(bodyText()[0]).toContain('—');
  });
});
