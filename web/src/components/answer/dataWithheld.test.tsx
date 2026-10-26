import { cleanup, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { DataTable } from '../parts/DataTable';
import type { TablePart } from '../../lib/contract';

afterEach(cleanup);

// Contract 1.11.0: a chat shared without data keeps columns + rowCount, rows are empty.
const withheld = {
  type: 'table', id: 't1', title: 'Denial rate by payer', rowCount: 7, rows: [], dataWithheld: true,
  columns: [{ key: 'payer', label: 'Payer', type: 'string' }, { key: 'rate', label: 'Denial rate', type: 'percent' }],
} as unknown as TablePart;

describe('dataWithheld table (share 1.11.0)', () => {
  it('shows "Data not shared (N rows)" instead of an empty grid, keeps headers, hides data actions', () => {
    render(<DataTable part={withheld} onOpen={() => {}} />);
    expect(screen.getByTestId('table-withheld')).toHaveTextContent('Data not shared (7 rows)');
    expect(screen.queryByText('Query returned 0 rows')).toBeNull();
    expect(screen.getByRole('columnheader', { name: /Payer/ })).toBeTruthy();
    expect(screen.queryByRole('button', { name: 'Download CSV' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Copy table' })).toBeNull();
    expect(screen.queryByRole('button', { name: 'Expand table' })).toBeNull();
    expect(screen.getByTestId('data-table')).toHaveTextContent('7 rows · data not shared');
    expect(screen.getByTestId('data-table')).not.toHaveTextContent('Showing 0 of 7');
  });
  it('a real empty result still says "Query returned 0 rows"', () => {
    render(<DataTable part={{ ...withheld, dataWithheld: undefined, rowCount: 0 } as TablePart} />);
    expect(screen.getByText('Query returned 0 rows')).toBeTruthy();
  });
});
