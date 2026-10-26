import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from '../../App';
import { reduce, newAssistantView, type AssistantView, type StreamEvent, type ToolCallPart } from '../../lib/contract';
import { AssistantTurn } from '../Message';
import { stepsOf, summarize, toStep, fmtUsd } from './normalize';
import { queriesOf } from './SqlInspector';
import { ANSWER_FIXTURES } from '../../fixtures/answer';

vi.mock('../../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

const viewOf = (id: string, streaming = false): AssistantView => {
  const f = ANSWER_FIXTURES.find((x) => x.id === id)!;
  let v = newAssistantView('a1');
  for (const s of f.steps) if (!('status' in s)) v = reduce(v, { type: 'part', part: s } as StreamEvent);
  return streaming ? v : reduce(v, { type: 'message.end', messageId: 'a1', finishReason: 'stop', usage: { ...f.usage, queries: f.queries, elapsedMs: 8400 } as never });
};
const noop = () => {};
afterEach(cleanup);
Element.prototype.scrollTo = function () {} as never;

describe('answer surface: normalize', () => {
  it('maps tool names to step kinds and summarizes', () => {
    const steps = stepsOf(viewOf('dup-denials'));
    expect(steps.map((s) => s.kind)).toEqual(['explore', 'sql', 'repair', 'candidates', 'check']);
    expect(steps[2].error).toMatch(/Binder Error/);
    expect(steps[3].candidates).toHaveLength(3);
    expect(steps[4].checks?.filter((c) => !c.passed)).toHaveLength(1);
    expect(summarize(steps, 12000)).toBe('Ran 2 queries, 1 repair, 1 check, compared candidates · 12s');
  });
  it('keeps unknown tools as "other" and formats money', () => {
    expect(toStep({ type: 'tool_call', id: 'x', tool: 'mystery', label: 'L', state: 'ok' } as ToolCallPart).kind).toBe('other');
    expect(fmtUsd(0.0042)).toBe('$0.0042');
    expect(fmtUsd(1.5)).toBe('$1.50');
  });
  it('links each table to the query that produced it', () => {
    const qs = queriesOf(viewOf('dup-denials'));
    const fixed = qs.find((q) => q.id === 'st-r1')!;
    expect(fixed.produces).toEqual(['Duplicate-claim denial rate by provider, Q3 2026']);
    expect(qs.find((q) => q.id === 'st-q1')!.state).toBe('error');
  });
});

describe('answer surface: components', () => {
  it('timeline is live while streaming and collapses to a summary when done', () => {
    const live = viewOf('dup-denials', true);
    const runningOnly = { ...live, parts: live.parts.filter((p) => p.type === 'reasoning' || p.type === 'tool_call') };
    const { rerender } = render(<AssistantTurn msg={runningOnly} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    const tl = screen.getByTestId('step-timeline');
    expect(tl).toHaveAttribute('data-live', 'true');
    expect(within(tl).getAllByTestId('step')).toHaveLength(5);
    expect(screen.getByTestId('step-summary')).toHaveAttribute('aria-expanded', 'true');
    rerender(<AssistantTurn msg={viewOf('dup-denials')} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.getByTestId('step-summary')).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByTestId('step-summary').textContent).toMatch(/^Ran 2 queries, 1 repair, 1 check, compared candidates · 8\.4s/);
    fireEvent.click(screen.getByTestId('step-summary'));
    expect(screen.getByTestId('repair-preview')).toHaveTextContent('Binder Error');
    const rows = screen.getAllByTestId('step');
    fireEvent.click(within(rows[2]).getAllByRole('button')[0]);
    expect(screen.getByTestId('repair-error')).toHaveTextContent('Binder Error');
    fireEvent.click(within(rows[3]).getAllByRole('button')[0]);
    expect(screen.getByTestId('candidates')).toHaveTextContent('chosen');
  });
  it('shows escalation, SQL under the table, inspector and cost footer', () => {
    render(<AssistantTurn msg={viewOf('dup-denials')} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.getByTestId('escalation')).toHaveTextContent('Haiku → Sonnet');
    fireEvent.click(within(screen.getByTestId('table-sql')).getByRole('button'));
    expect(screen.getByTestId('table-sql')).toHaveTextContent('COUNT(*) FILTER');
    expect(screen.getByTestId('meta-line')).toHaveTextContent('3 queries · 8.4s · $0.04');
    fireEvent.click(screen.getByTestId('meta-line'));
    expect(screen.getByTestId('meta-popover')).toHaveTextContent('18k in');
    fireEvent.click(screen.getByTestId('open-sql'));
    const insp = screen.getByTestId('sql-inspector');
    expect(within(insp).getAllByRole('tab').length).toBe(3);
    expect(insp).toHaveTextContent('produced “Duplicate-claim denial rate by provider, Q3 2026”');
  });
  it('abstain is a designed state with clickable rephrasings', () => {
    const onSend = vi.fn();
    render(<AssistantTurn msg={viewOf('abstain')} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={onSend} />);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByTestId('abstain')).toHaveTextContent('What would be needed');
    fireEvent.click(screen.getAllByTestId('rephrase-chip')[0]);
    expect(onSend).toHaveBeenCalledWith('Which clinics have the highest denial rate?');
  });
  it('marks steps still running at the end as stopped', () => {
    const live = viewOf('dup-denials', true);
    const cut = { ...live, parts: [...live.parts.slice(0, 2), { ...(live.parts[2] as ToolCallPart), state: 'running' as const }], streaming: false, finishReason: 'aborted' as const };
    render(<AssistantTurn msg={cut} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.getByTestId('step-summary').textContent).toMatch(/1 stopped/);
  });
  it('stopped answers show a Retry line', () => {
    const onRegenerate = vi.fn();
    const v = { ...viewOf('abstain'), finishReason: 'aborted' as const };
    render(<AssistantTurn msg={v} isLast onOpen={noop} onRegenerate={onRegenerate} onEdit={noop} />);
    fireEvent.click(within(screen.getByTestId('stopped')).getByRole('button', { name: /Retry/ }));
    expect(onRegenerate).toHaveBeenCalled();
  });
});

describe('answer surface: clarify flow (App)', () => {
  it('asks a clarifying question, a chip answers it and the run resumes', async () => {
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    render(<App />);
    fireEvent.click(screen.getByRole('button', { name: 'Who are our top providers?' }));
    await waitFor(() => expect(screen.getByTestId('clarify')).toBeInTheDocument(), { timeout: 4000 });
    await waitFor(() => expect(screen.getAllByTestId('clarify-chip')[1]).toBeEnabled());
    await act(async () => { fireEvent.click(screen.getAllByTestId('clarify-chip')[1]); });
    await waitFor(() => expect(screen.getByTestId('clarify-answered')).toBeInTheDocument());
    await waitFor(() => expect(screen.getAllByTestId('msg-user')).toHaveLength(2));
    await waitFor(() => expect(screen.getAllByTestId('data-table').length).toBeGreaterThan(0), { timeout: 5000 });
    expect(screen.getAllByTestId('clarify-chip').every((b) => (b as HTMLButtonElement).disabled)).toBe(true);
  });
});
