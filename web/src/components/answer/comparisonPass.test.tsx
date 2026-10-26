import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { printOne } from '../Message';
import { ErrorView } from '../parts/misc';
import { DataTable } from '../parts/DataTable';
import type { ErrorPart, TablePart } from '../../lib/contract';

afterEach(() => { cleanup(); document.body.innerHTML = ''; });

describe('comparison pass', () => {
  it('Print this answer marks only the question + answer while the dialog is open', () => {
    document.body.innerHTML = '<div data-message-id="u0"></div><div data-message-id="a0"></div><div data-message-id="u1">Q</div><div data-message-id="a1"><div id="ans">A</div></div>';
    const seen: string[] = [];
    const spy = vi.spyOn(window, 'print').mockImplementation(() => {
      seen.push(document.body.hasAttribute('data-print-one') ? 'one' : 'all');
      document.querySelectorAll('[data-print-keep]').forEach((e) => seen.push(e.getAttribute('data-message-id')!));
    });
    printOne(document.getElementById('ans'));
    expect(seen).toEqual(['one', 'u1', 'a1']);
    window.dispatchEvent(new Event('afterprint'));
    expect(document.body.hasAttribute('data-print-one')).toBe(false);
    expect(document.querySelectorAll('[data-print-keep]').length).toBe(0);
    spy.mockRestore();
  });
  it('error card: quiet title, one primary Retry', () => {
    const retry = vi.fn();
    render(<ErrorView part={{ type: 'error', id: 'e', code: 'internal', title: 'Analyst unavailable', message: 'Try again.', retryable: true } as ErrorPart} onRetry={retry} />);
    expect(screen.getByRole('alert')).toHaveTextContent('Analyst unavailable');
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(retry).toHaveBeenCalled();
  });
  it('null cells render a muted dash with a "No value" hint', () => {
    const part = { type: 'table', id: 't', rowCount: 1, rows: [['Aetna', null]], columns: [{ key: 'p', label: 'Payer', type: 'string' }, { key: 'r', label: 'Rate', type: 'percent' }] } as unknown as TablePart;
    render(<DataTable part={part} />);
    const dash = screen.getByTitle('No value');
    expect(dash).toHaveTextContent('—');
    expect(dash.className).toContain('text-fg-subtle');
  });
});
