import { readFileSync } from 'node:fs';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { ErrorView } from '../parts/misc';
import { DataTable, shownTitle } from '../parts/DataTable';
import { StepTimeline } from './StepTimeline';
import type { ErrorPart, TablePart } from '../../lib/contract';

afterEach(cleanup);
const FILLS = /\bbg-(danger|red|rose|pink|black|fg)\b|bg-\[#|border-(danger|red)/;

describe('QA C9 fidelity (F1, F2, F7, F9, F10)', () => {
  it('QA-A23: the error turn is a quiet inline notice; Retry is a ghost text control', () => {
    const onRetry = vi.fn();
    render(<ErrorView part={{ type: 'error', id: 'e', code: 'internal', title: 'Connection lost', message: 'Response interrupted.', retryable: true } as ErrorPart} onRetry={onRetry} />);
    const box = screen.getByTestId('error');
    for (const el of [box, ...box.querySelectorAll('*')]) {
      const cls = el.getAttribute('class') ?? '';
      expect(cls, cls).not.toMatch(FILLS);
      expect(cls, cls).not.toMatch(/\brounded-(xl|2xl|full)\b/);
    }
    expect(box.className).not.toMatch(/\bborder\b|\bp-\d/);
    const retry = screen.getByTestId('error-retry');
    expect(retry.className).not.toMatch(/\bborder\b/);
    expect(retry.className).toMatch(/text-fg-muted/);
    retry.click();
    expect(onRetry).toHaveBeenCalled();
  });

  const table = { type: 'table', id: 't', title: 'Result', columns: [{ key: 'a', label: 'Payer', type: 'string' }], rows: [['Synthetic A']], rowCount: 1 } as unknown as TablePart;
  it('QA-A24: inline tables have no frame or caption; a real title stays; the panel keeps its frame', () => {
    render(<DataTable part={table} onOpen={() => {}} />);
    const t = screen.getByTestId('data-table');
    expect(t.dataset.frame).toBe('inline');
    expect(t.className).not.toMatch(/\bborder\b|rounded|bg-elev/);
    expect(screen.getByTestId('table-title').textContent).toBe('');
    cleanup();
    render(<DataTable part={{ ...table, title: 'Denial rate by payer' }} />);
    expect(screen.getByTestId('table-title')).toHaveTextContent('Denial rate by payer');
    expect(['Result', 'results', ' Query result ', '', undefined].map(shownTitle)).toEqual(['', '', '', '', '']);
    expect(shownTitle('Results by payer')).toBe('Results by payer');
  });

  it('QA-A25: the running step marker does not ripple', () => {
    render(<StepTimeline steps={[{ id: 's', kind: 'sql', state: 'running', label: 'Running query' }]} live />);
    for (const el of document.querySelectorAll('*')) expect(el.getAttribute('class') ?? '').not.toMatch(/animate-(ping|bounce)/);
  });

  it('QA-A26: the timing bar is one hue in steps, no hex rainbow', () => {
    const src = readFileSync('src/components/answer/AnswerMeta.tsx', 'utf8');
    expect(src.match(/#[0-9a-f]{6}\b/gi) ?? []).toEqual([]);
    expect(src).toMatch(/const BAR = \[[\d, ]+\]\.map\(\(p\) => `color-mix\(in srgb, var\(--fg\)/);
  });

  it('QA-A27: the artifact tabs only show a focus ring for keyboard users', () => {
    expect(readFileSync('src/index.css', 'utf8')).toContain('html:not([data-kbd]) .cc-quiet-focus:focus-visible { outline: none; }');
    expect(readFileSync('src/components/ArtifactPanel.tsx', 'utf8')).toMatch(/role="tab".*cc-quiet-focus/);
  });
});

describe('table header weight: data tables match markdown tables', () => {
  it('600 weight, the stronger header rule, hairline body rules, same padding', () => {
    render(<DataTable part={{ type: 'table', id: 'h', columns: [{ key: 'a', label: 'Payer', type: 'string' }], rows: [['Synthetic A']], rowCount: 1 } as unknown as TablePart} />);
    const th = screen.getByRole('columnheader', { name: /Payer/ });
    expect(th.className).toMatch(/\bfont-semibold\b/);
    expect(th.className).toMatch(/\bborder-line-strong\b/);
    expect(th.className).toMatch(/\bpx-3 py-2\b/);
    expect(screen.getByRole('cell', { name: 'Synthetic A' }).className).toMatch(/\bborder-line\b(?!-)/);
    const css = readFileSync('src/index.css', 'utf8');
    expect(css).toMatch(/\.prose-chat :where\(th\) \{[^}]*font-weight: 600;[^}]*border-bottom: 1px solid var\(--border-strong\)/);
    expect(css).toMatch(/\.prose-chat :where\(th\) \{[^}]*padding: 0\.5em 0\.75em/);
  });
});
