import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AssistantTurn } from '../Message';
import { stepsOf, summarize } from './normalize';
import { SourcesChip } from './Sources';
import { AbstainCard } from './States';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent as V1Event } from '../../api/contract';
import type { Step } from './provisional';

vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));
afterEach(cleanup);
const v1 = (evs: V1Event[]) => evs.reduce((m, e) => reduceV1(m, e), newAssistantViewV1('a1') as AssistantViewV1);
const ex = (stepId: string, probe: string, target?: string) => ({ type: 'step.explore', stepId, probe, target, summary: 'synthetic', elapsedMs: 10 }) as V1Event;

/** Peter 11:57 PM: the user never picks a DB; the answer surface says what the analyst itself looked at. */
describe('BACKEND-FIT: the analyst chooses the data, not the user', () => {
  it('explore steps read "Looked at sem.x"; listing reads as the analyst finding tables', () => {
    const steps = stepsOf(v1([ex('x0', 'list_views'), ex('x1', 'describe_view', 'sem.claims'), ex('x2', 'sample_values', 'sem.payers')]));
    expect(steps.map((s) => s.label)).toEqual(['Found the tables it can read', 'Looked at sem.claims', 'Looked at sem.payers']);
    // A value/term lookup (live.py maps lookup_values → sample_values with a term target) is not a relation read.
    expect(stepsOf(v1([ex('x3', 'sample_values', 'denied')]))[0].label).toBe('Sampled values · denied');
  });
  it('a data escalation reads as using the full EDI data, with the stated reason', () => {
    const esc = (stepId: string, summary: string) => ({ type: 'step.explore', stepId, probe: 'request_escalation', summary, elapsedMs: 5 }) as V1Event;
    const steps = stepsOf(v1([esc('e0', 'Failed: request_escalation requires a reason'), esc('e1', 'Reason: sem.* has no file parse status')]));
    expect(steps.map((s) => [s.label, s.message])).toEqual([
      ['Asked for the full EDI data', 'Failed: request_escalation requires a reason'],
      ['Used the full EDI data', 'Reason: sem.* has no file parse status'],
    ]);
  });
  it('an exploration-only run summarizes as the relations it read', () => {
    const st = (target?: string): Step => ({ id: target ?? 'n', kind: 'explore', state: 'ok', label: 'x', target });
    expect(summarize([st('sem.claims')])).toBe('Looked at sem.claims');
    expect(summarize([st('sem.claims'), st('sem.payers'), st('sem.claims')])).toBe('Looked at sem.claims, sem.payers');
    expect(summarize([st('a'), st('b'), st('c'), st('d')])).toBe('Looked at a, b +2 more');
    expect(summarize([st()])).toBe('Looked at the data');
  });
  it('the sources chip says "Looked at <relation>" and the popover credits the analyst', () => {
    render(<SourcesChip sources={[{ id: 's1', kind: 'view', name: 'sem.claims', label: 'Claims', role: 'answer' }]} parts={[]} />);
    const chip = screen.getByTestId('sources-chip');
    expect(chip.textContent).toBe('Looked atsem.claims');
    fireEvent.click(chip);
    expect(screen.getByTestId('sources-popover')).toHaveTextContent('What the analyst used for this answer');
  });
  it('no answer-surface wording or control implies the user chose, scoped or can change the data', () => {
    const { container } = render(
      <>
        <AssistantTurn msg={v1([ex('x1', 'describe_view', 'sem.claims'), { type: 'message.end', messageId: 'a1', finishReason: 'stop' } as V1Event])} isLast onOpen={() => {}} onRegenerate={() => {}} onEdit={() => {}} />
        <AbstainCard part={{ type: 'abstain', id: 'ab', category: 'out_of_scope', reason: 'Synthetic.', suggestions: [] } as never} />
      </>,
    );
    const text = container.textContent ?? '';
    expect(text).not.toMatch(/scoped session|your scope|change scope|choose (a )?(data|database|source)|select (a )?(data|database|source)|data source|from this data\b/i);
    expect(container.querySelector('[data-testid="scope-chip"], [aria-label*="scope" i], [aria-label*="data source" i]')).toBeNull();
  });
});
