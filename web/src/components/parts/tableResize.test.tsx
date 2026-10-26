import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import type { TablePart } from '../../lib/contract';
import { DataTable, MAX_COL, MIN_COL, clampCol } from './DataTable';
import { withTooltips } from '../../render/chartSpec';

afterEach(cleanup);
const table: TablePart = {
  id: 't1', type: 'table', title: 'Denials', rowCount: 2,
  columns: [{ key: 'payer', label: 'Payer', type: 'string' }, { key: 'n', label: 'Claims', type: 'integer' }],
  rows: [['PAYER-SYN-LONG-NAME-ALPHA', 12], ['PAYER-B', 7]],
} as TablePart;

describe('table column resize', () => {
  it('each header has a keyboard-operable resize separator', () => {
    render(<DataTable part={table} />);
    const seps = screen.getAllByRole('separator', { name: /Resize column/ });
    expect(seps).toHaveLength(2);
    expect(seps[0].getAttribute('tabindex')).toBe('0');
    expect(seps[0].getAttribute('aria-valuemin')).toBe(String(MIN_COL));
  });
  it('arrow keys set a fixed width on header and cells; Home resets', () => {
    render(<DataTable part={table} />);
    const sep = screen.getByRole('separator', { name: 'Resize column Payer' });
    const th = sep.closest('th')!;
    fireEvent.keyDown(sep, { key: 'ArrowRight' }); // jsdom width 0 → clamps to MIN_COL+? (0+16 → 56)
    expect(th.style.width).toBe(`${MIN_COL}px`);
    fireEvent.keyDown(sep, { key: 'ArrowRight', shiftKey: true });
    expect(th.style.width).toBe(`${MIN_COL + 48}px`);
    const td = screen.getByText('PAYER-SYN-LONG-NAME-ALPHA').closest('td')!;
    expect(td.style.maxWidth).toBe(`${MIN_COL + 48}px`);
    expect(td.className).toContain('text-ellipsis');
    expect(sep.getAttribute('aria-valuenow')).toBe(String(MIN_COL + 48));
    fireEvent.keyDown(sep, { key: 'Home' });
    expect(th.style.width).toBe('');
  });
  it('pointer drag resizes; clicking the handle does not sort', () => {
    render(<DataTable part={table} />);
    const sep = screen.getByRole('separator', { name: 'Resize column Claims' });
    fireEvent.pointerDown(sep, { clientX: 100 });
    fireEvent.pointerMove(window, { clientX: 300 });
    fireEvent.pointerUp(window);
    expect(sep.closest('th')!.style.width).toBe('200px');
    fireEvent.click(sep);
    expect(sep.closest('th')!.getAttribute('aria-sort')).toBe('none');
  });
  it('clamps', () => { expect(clampCol(1)).toBe(MIN_COL); expect(clampCol(9999)).toBe(MAX_COL); });
});

describe('chart tooltips default', () => {
  it('server tooltips borrow axis titles and formats (no raw column keys)', () => {
    const spec = { mark: { type: 'bar', tooltip: true }, encoding: {
      x: { field: 'carc_code', type: 'nominal', title: 'CARC code' },
      y: { field: 'rate', type: 'quantitative', title: 'Denial rate', axis: { format: '.1%' } },
      tooltip: [{ field: 'carc_code', type: 'nominal' }, { field: 'rate', type: 'quantitative' }, { field: 'other', type: 'nominal' }] } };
    const tt = (withTooltips(spec).encoding as { tooltip: unknown[] }).tooltip;
    expect(tt).toEqual([{ field: 'carc_code', type: 'nominal', title: 'CARC code' }, { field: 'rate', type: 'quantitative', title: 'Denial rate', format: '.1%' }, { field: 'other', type: 'nominal' }]);
  });
  it('adds mark.tooltip to plain single views only', () => {
    expect(withTooltips({ mark: 'bar', encoding: {} })).toMatchObject({ mark: { type: 'bar', tooltip: true } });
    expect(withTooltips({ mark: { type: 'line', point: true } })).toMatchObject({ mark: { type: 'line', point: true, tooltip: true } });
    const explicit = { mark: 'bar', encoding: { tooltip: [{ field: 'a', title: 'A' }] } };
    expect(withTooltips(explicit)).toBe(explicit);
    const off = { mark: { type: 'bar', tooltip: false } };
    expect(withTooltips(off)).toBe(off);
    const layered = { layer: [{ mark: 'bar' }] };
    expect(withTooltips(layered)).toBe(layered);
  });
});
