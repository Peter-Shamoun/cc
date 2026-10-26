import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import type { AssistantView, ChartPart, TablePart } from '../../lib/contract';
import { reduce, newAssistantView, type StreamEvent } from '../../lib/contract';
import { ChartView } from './ChartView';
import { DataTable } from './DataTable';
import { AssistantTurn } from '../Message';

let resolveEmbed: (() => void) | null = null;
vi.mock('vega-embed', () => ({
  default: () => new Promise((res) => { resolveEmbed = () => res({ view: { finalize() {}, toImageURL: async () => '' } }); }),
}));
afterEach(cleanup);

const chart: ChartPart = { type: 'chart', id: 'c1', title: 'Monthly denial rate, top 3 payers by volume', alt: 'line chart', spec: { mark: 'line' }, data: { values: [{ m: 1, v: 2 }] } } as unknown as ChartPart;
const table: TablePart = {
  type: 'table', id: 't1', title: 'Wide', rowCount: 1,
  columns: ['a', 'b', 'c', 'd'].map((k) => ({ key: k, label: k.toUpperCase(), type: 'string' })),
  rows: [['1', '2', '3', '4']],
} as unknown as TablePart;

describe('QA-A2 chart skeleton', () => {
  it('shows a skeleton at final height until vega has drawn, then removes it', async () => {
    render(<ChartView part={chart} />);
    expect(screen.getByTestId('chart-skeleton')).toBeInTheDocument();
    await waitFor(() => expect(resolveEmbed).not.toBeNull());
    await act(async () => { resolveEmbed!(); });
    await waitFor(() => expect(screen.queryByTestId('chart-skeleton')).toBeNull());
  });
  it('does not truncate the chart title (QA-A3)', () => {
    render(<ChartView part={chart} />);
    expect(screen.getByText(chart.title!).className).not.toMatch(/\btruncate\b/);
  });
});

describe('QA-A4 table scroll fade', () => {
  it('fades the right edge when columns overflow and the left edge after scrolling', () => {
    const sw = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'scrollWidth');
    const cw = Object.getOwnPropertyDescriptor(HTMLElement.prototype, 'clientWidth');
    Object.defineProperty(HTMLElement.prototype, 'scrollWidth', { configurable: true, get: () => 600 });
    Object.defineProperty(HTMLElement.prototype, 'clientWidth', { configurable: true, get: () => 300 });
    try {
      render(<DataTable part={table} />);
      expect(screen.getByTestId('table-fade-right')).toHaveAttribute('data-on', 'true');
      expect(screen.getByTestId('table-fade-left')).not.toHaveAttribute('data-on');
      const scroller = screen.getByTestId('table-fade-right').previousElementSibling!.previousElementSibling as HTMLElement;
      scroller.scrollLeft = 300;
      fireEvent.scroll(scroller);
      expect(screen.getByTestId('table-fade-left')).toHaveAttribute('data-on', 'true');
      expect(screen.getByTestId('table-fade-right')).not.toHaveAttribute('data-on');
    } finally {
      if (sw) Object.defineProperty(HTMLElement.prototype, 'scrollWidth', sw);
      if (cw) Object.defineProperty(HTMLElement.prototype, 'clientWidth', cw);
    }
  });
});

describe('QA-A5 feedback panel', () => {
  it('scrolls into view, focuses the first reason, and Escape closes it', () => {
    const spy = vi.fn();
    Element.prototype.scrollIntoView = spy as never;
    let v: AssistantView = newAssistantView('a1');
    v = reduce(v, { type: 'part', part: { type: 'text', id: 'x', text: 'Hello' } } as StreamEvent);
    v = reduce(v, { type: 'message.end', messageId: 'a1', finishReason: 'stop' } as StreamEvent);
    render(<AssistantTurn msg={v} isLast onOpen={() => {}} onRegenerate={() => {}} onEdit={() => {}} />);
    fireEvent.click(screen.getByRole('button', { name: 'Bad response' }));
    const panel = screen.getByTestId('feedback-panel');
    expect(spy).toHaveBeenCalledWith(expect.objectContaining({ block: 'nearest' }));
    expect(document.activeElement).toBe(panel.querySelector('button[aria-pressed]'));
    fireEvent.keyDown(panel, { key: 'Escape' });
    expect(screen.queryByTestId('feedback-panel')).toBeNull();
  });
});
