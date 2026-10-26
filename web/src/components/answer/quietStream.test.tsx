import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { StoppedNote } from './States';

afterEach(cleanup);
const css = readFileSync(resolve(__dirname, '../../index.css'), 'utf8');

/** ChatGPT hands-on 2026-10-26: no caret (trailing lines fade), and Stop just cuts the text. */
describe('quiet streaming edge and stop', () => {
  it('no caret dot after the last text block; the last block gets a trailing fade mask instead', () => {
    expect(css).not.toMatch(/\.streaming-caret[^{]*:is\(p, h1[^{]*::after/);
    expect(css).toMatch(/\.streaming-caret > div > div:last-child \{[^}]*mask-image: linear-gradient\(to bottom, #000 calc\(100% - 3em\)/);
    // The only dot left is the empty placeholder before any text.
    expect(css.match(/\.streaming-caret[^{,]*::after/g)).toEqual(['.streaming-caret:empty::after']);
  });
  it('stopped is one tiny muted word: no icon, no sentence, no box', () => {
    render(<StoppedNote />);
    const s = screen.getByTestId('stopped');
    expect(s.textContent).toBe('Stopped');
    expect(s).toHaveClass('text-xs', 'text-fg-subtle');
    expect(s.querySelector('svg')).toBeNull();
    expect(s.className).not.toMatch(/border|bg-/);
  });
  it('an empty stopped turn still offers one quiet Retry', () => {
    const retry = vi.fn();
    render(<StoppedNote onRetry={retry} />);
    fireEvent.click(screen.getByRole('button', { name: /Retry/ }));
    expect(retry).toHaveBeenCalledOnce();
    expect(screen.getByRole('button', { name: /Retry/ }).className).not.toMatch(/\bborder\b/);
  });
});

import { DataTable } from '../parts/DataTable';
import type { TablePart } from '../../lib/contract';

describe('tables like ChatGPT (hands-on 2026-10-26): no outer border, tools only on hover', () => {
  const table = { type: 'table', id: 't', columns: [{ key: 'a', label: 'Payer', type: 'string' }, { key: 'b', label: 'Rate', type: 'number' }], rows: [['Synthetic A', 0.1], ['Synthetic B', 0.2], ['Synthetic C', 0.3]], rowCount: 3 } as unknown as TablePart;
  it('inline frame has no border, card fill or zebra; toolbar is hidden until hover/focus', () => {
    render(<DataTable part={table} onOpen={() => {}} />);
    const frame = screen.getByTestId('data-table');
    expect(frame.dataset.frame).toBe('inline');
    expect(frame.className).not.toMatch(/\bborder\b|\brounded|bg-elev/);
    expect(frame.querySelector('[class*="even:"], [class*="odd:"]')).toBeNull();
    const tools = screen.getByRole('button', { name: 'Copy table' }).parentElement!;
    expect(tools.className).toMatch(/opacity-0/);
    expect(tools.className).toMatch(/group-hover\/table:opacity-100/);
    expect(tools.className).toMatch(/group-focus-within\/table:opacity-100/);
  });
  it('markdown tables in prose: header rule and row hairlines only, no outer border', () => {
    const rule = (sel: string) => css.match(new RegExp(`\\.prose-chat :where\\(${sel}\\) \\{([^}]*)\\}`))?.[1] ?? '';
    expect(rule('table')).not.toMatch(/border:/);
    expect(rule('th')).toMatch(/border-bottom: 1px solid var\(--border-strong\)/);
    expect(rule('th')).not.toMatch(/background/);
    expect(rule('td')).toMatch(/border-bottom: 1px solid var\(--border\)/);
  });
});
