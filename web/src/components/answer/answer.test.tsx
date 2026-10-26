import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import App from '../../App';
import { reduce, newAssistantView, type AssistantView, type StreamEvent, type ToolCallPart } from '../../lib/contract';
import { AssistantTurn, messageMarkdown } from '../Message';
import { stepsOf, summarize, toStep, fmtUsd } from './normalize';
import { queriesOf } from './SqlInspector';
import { ANSWER_FIXTURES } from '../../fixtures/answer';
import { DUP_V1_ANSWER, DUP_V1_STEPS } from '../../fixtures/answerV1';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent as V1Event } from '../../api/contract';
import { ClarifyCard } from './States';

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
    expect(summarize(steps, 12000)).toBe('Ran 2 queries, 1 repair, 4 checks, compared candidates · 12s');
  });
  it('a finished check step with a failed check is a warning; copy includes abstain text', () => {
    expect(stepsOf(viewOf('dup-denials')).find((s) => s.kind === 'check')!.state).toBe('warn');
    const md = messageMarkdown(viewOf('abstain'));
    expect(md).toMatch(/claims and remittance data/);
    expect(md).toMatch(/- Which clinics have the highest denial rate\?/);
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
    expect(screen.getByTestId('step-summary').textContent).toMatch(/^Ran 2 queries, 1 repair, 4 checks, compared candidates · 8\.4s/);
    fireEvent.click(screen.getByTestId('step-summary'));
    expect(screen.getByTestId('repair-preview')).toHaveTextContent('Binder Error');
    const rows = screen.getAllByTestId('step');
    fireEvent.click(within(rows[2]).getAllByRole('button')[0]);
    expect(screen.getByTestId('repair-error')).toHaveTextContent('Binder Error');
    fireEvent.click(within(rows[3]).getAllByRole('button')[0]);
    expect(screen.getByTestId('candidates')).toHaveTextContent('chosen');
  });
  it('shows SQL under the table, inspector and cost footer', () => {
    render(<AssistantTurn msg={viewOf('dup-denials')} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    fireEvent.click(within(screen.getByTestId('table-sql')).getByRole('button'));
    expect(screen.getByTestId('table-sql')).toHaveTextContent('COUNT(*) FILTER');
    expect(screen.getByTestId('meta-line')).toHaveTextContent('3 queries · 8.4s · ~$0.04');
    fireEvent.click(screen.getByTestId('meta-line'));
    expect(screen.getByTestId('meta-popover')).toHaveTextContent('18k in');
    expect(screen.getByTestId('meta-models')).toHaveTextContent('Sonnet 4.5');
    fireEvent.click(screen.getByTestId('open-sql'));
    const insp = screen.getByTestId('sql-inspector');
    expect(within(insp).getAllByRole('tab').length).toBe(3);
    expect(insp).toHaveTextContent('produced “Duplicate-claim denial rate by provider, Q3 2026”');
  });
  it('abstain is a designed state with clickable rephrasings', () => {
    const onSend = vi.fn();
    render(<AssistantTurn msg={viewOf('abstain')} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={onSend} />);
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByTestId('abstain')).toHaveAttribute('data-category', 'data_unavailable');
    expect(screen.getByTestId('abstain-details')).toHaveTextContent('What would be needed');
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

const v1 = (evs: V1Event[]) => evs.reduce((m, e) => reduceV1(m, e), newAssistantViewV1('a1') as AssistantViewV1);

describe('answer surface: contract v1 events', () => {
  it('maps step.* events into the timeline, merges checks, and pairs repair with its re-run', () => {
    const steps = stepsOf(v1([...DUP_V1_STEPS, ...DUP_V1_ANSWER]));
    expect(steps.map((s) => s.kind)).toEqual(['explore', 'explore', 'sql', 'repair', 'sql', 'sql', 'candidates', 'check', 'escalation']);
    const repair = steps[3];
    expect(repair.state).toBe('ok');
    expect(repair.previousSql).toMatch(/month, COUNT/);
    expect(repair.error).toMatch(/Binder Error/);
    expect(steps[7].checks).toHaveLength(3);
    expect(steps[7].state).toBe('warn');
    expect(steps[6].candidates?.find((c) => c.chosen)?.id).toBe('a');
    expect(summarize(steps, 8400)).toBe('Ran 5 queries, 1 repair, 3 checks, compared candidates · 8.4s');
  });
  it('renders live steps, then escalation pill, v1 table link and rich usage', () => {
    const { rerender } = render(<AssistantTurn msg={v1(DUP_V1_STEPS.slice(0, 8))} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.getByTestId('step-timeline')).toHaveAttribute('data-live', 'true');
    expect(screen.getByTestId('step-summary')).toHaveTextContent('Re-ran repaired query…');
    expect(screen.getByTestId('table-skeleton')).toBeInTheDocument();
    rerender(<AssistantTurn msg={v1([...DUP_V1_STEPS, ...DUP_V1_ANSWER])} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.getByTestId('step-summary')).toHaveAttribute('aria-expanded', 'false');
    expect(screen.getByTestId('escalation')).toHaveTextContent('Escalated to Sonnet 4.5');
    fireEvent.click(within(screen.getByTestId('escalation')).getByRole('button'));
    expect(screen.getByTestId('escalation')).toHaveTextContent('Haiku 4.5 → Sonnet 4.5');
    fireEvent.click(within(screen.getByTestId('table-sql')).getByRole('button'));
    expect(screen.getByTestId('table-sql')).toHaveTextContent('COUNT(*) FILTER');
    expect(screen.getByTestId('meta-line')).toHaveTextContent('4 queries · 8.4s · ~$0.04');
    fireEvent.click(screen.getByTestId('meta-line'));
    const pop = screen.getByTestId('meta-popover');
    expect(pop).toHaveTextContent('First token');
    expect(pop).toHaveTextContent('Model');
    expect(pop).toHaveTextContent('est.');
    fireEvent.click(screen.getByTestId('open-sql'));
    expect(screen.getByTestId('sql-inspector')).toHaveTextContent('produced “Duplicate-claim denial rate by provider, Q3 2026”');
  });
  it('aborted v1 runs mark in-flight steps as stopped', () => {
    const evs = [...DUP_V1_STEPS.slice(0, 5), { type: 'message.end', messageId: 'm-v1', finishReason: 'aborted', usage: { queries: 1, elapsedMs: 900 }, seq: 99 } as V1Event];
    render(<AssistantTurn msg={v1(evs)} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.getByTestId('step-summary').textContent).toMatch(/1 stopped/);
    expect(screen.getByTestId('stopped')).toBeInTheDocument();
  });
  it('clarification replies carry ChatRequest.resume (single and multi-select)', () => {
    const onReply = vi.fn();
    const part = { type: 'clarification' as const, id: 'c', clarificationId: 'clar-1', question: 'Which payers?', allowFreeText: true, multiSelect: true, choices: [{ id: 'p1', label: 'Medicare' }, { id: 'p2', label: 'Medicaid' }] };
    render(<ClarifyCard part={part} onReply={onReply} active />);
    const chips = screen.getAllByTestId('clarify-chip');
    fireEvent.click(chips[0]); fireEvent.click(chips[1]);
    fireEvent.click(screen.getByTestId('clarify-submit'));
    expect(onReply).toHaveBeenCalledWith('Medicare, Medicaid', { clarificationId: 'clar-1', choiceIds: ['p1', 'p2'] });
    cleanup();
    const one = vi.fn();
    render(<ClarifyCard part={{ ...part, multiSelect: false }} onReply={one} active />);
    fireEvent.change(screen.getByLabelText('Answer in your own words'), { target: { value: 'Commercial only' } });
    fireEvent.submit(screen.getByLabelText('Answer in your own words').closest('form')!);
    expect(one).toHaveBeenCalledWith('Commercial only', { clarificationId: 'clar-1', text: 'Commercial only' });
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
