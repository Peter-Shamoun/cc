import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { newAssistantView, reduce, type AssistantView, type StreamEvent } from '../../lib/contract';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent as V1Event } from '../../api/contract';
import { AssistantTurn } from '../Message';
import { CodeBlock, CodePartView } from '../parts/CodeBlock';
import { stepsOf } from './normalize';
import { queriesOf } from './SqlInspector';
import { StepTimeline } from './StepTimeline';
import { sqlForClipboard } from '../../lib/feedback';

vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
Element.prototype.scrollTo = function () {} as never;
const noop = () => {};
afterEach(cleanup);

// Synthetic only. Contract 1.14.0 §10: literals that could be patient data are shown as `?`.
const MASKED = "SELECT payer, count(*) FROM claims WHERE member_id = ? AND dob = ? GROUP BY 1";
const v1 = (evs: V1Event[]) => evs.reduce((m, e) => reduceV1(m, e), newAssistantViewV1('a1') as AssistantViewV1);
const start = { type: 'message.start', messageId: 'a1', conversationId: 'c', contractVersion: '1.14.0', seq: 0 } as V1Event;

const viewParts = (parts: Record<string, unknown>[]): AssistantView => {
  let v = newAssistantView('a1');
  for (const part of parts) v = reduce(v, { type: 'part', part } as unknown as StreamEvent);
  return reduce(v, { type: 'message.end', messageId: 'a1', finishReason: 'stop', usage: { queries: 1, elapsedMs: 10 } as never });
};
const table = { type: 'table', id: 't1', title: 'Claims by payer', sqlPartId: 'q1', columns: [{ key: 'payer', label: 'Payer', type: 'string' }, { key: 'n', label: 'Claims', type: 'integer' }], rows: [['Synthetic A', 3]], rowCount: 1 };
const query = (extra: Record<string, unknown> = {}) => ({ type: 'code', id: 'q1', language: 'sql', role: 'query', code: MASKED, ...extra });

describe('maskedLiterals is carried from the wire (1.14.0)', () => {
  it('sql_running, repair_attempt and candidates keep their counts; zero/garbage is ignored', () => {
    const steps = stepsOf(v1([
      start,
      { type: 'step.sql_running', stepId: 's1', sql: MASKED, purpose: 'answer', attempt: 1, maskedLiterals: 2, seq: 1 } as unknown as V1Event,
      { type: 'step.sql_result', stepId: 's1', ok: false, elapsedMs: 5, error: { code: 'binder_error', message: 'no column' }, seq: 2 } as unknown as V1Event,
      { type: 'step.repair_attempt', stepId: 'r1', failedStepId: 's1', attempt: 2, maxAttempts: 3, error: { code: 'binder_error', message: 'no column' }, revisedSql: MASKED, maskedLiterals: 1, seq: 3 } as unknown as V1Event,
      { type: 'step.candidates', stepId: 'c1', method: 'execution_agreement', chosenId: 'a', candidates: [
        { candidateId: 'a', sql: MASKED, ok: true, maskedLiterals: 4 }, { candidateId: 'b', sql: 'SELECT 1', ok: true, maskedLiterals: 0 }, { candidateId: 'c', sql: 'SELECT 2', ok: true, maskedLiterals: 'x' },
      ], seq: 4 } as unknown as V1Event,
    ]));
    expect(steps.find((s) => s.id === 's1')?.maskedLiterals).toBe(2);
    expect(steps.find((s) => s.kind === 'repair')?.maskedLiterals).toBe(1);
    expect(steps.find((s) => s.kind === 'candidates')?.candidates?.map((c) => c.maskedLiterals)).toEqual([4, undefined, undefined]);
  });
  it('queriesOf carries it from steps and from role=query code parts', () => {
    expect(queriesOf(viewParts([query({ maskedLiterals: 2 }), table]))[0].maskedLiterals).toBe(2);
    expect(queriesOf(viewParts([query(), table]))[0].maskedLiterals).toBeUndefined();
  });
});

describe('"Patient values hidden" sits next to masked SQL', () => {
  it('CodeBlock: muted note with an explanatory title only when count > 0', () => {
    render(<CodeBlock code={MASKED} language="sql" masked={2} />);
    const note = screen.getByTestId('sql-masked');
    expect(note).toHaveTextContent('Patient values hidden');
    expect(note.getAttribute('title')).toMatch(/2 values .* shown as \?/);
    expect(note.className).toMatch(/text-fg-subtle/);
    cleanup();
    render(<CodeBlock code="SELECT 1" language="sql" masked={0} />);
    expect(screen.queryByTestId('sql-masked')).toBeNull();
    cleanup();
    render(<CodeBlock code="SELECT 1" language="sql" />);
    expect(screen.queryByTestId('sql-masked')).toBeNull();
  });
  it('CodePartView (role=query) shows it once expanded', () => {
    render(<CodePartView part={query({ maskedLiterals: 3 }) as never} />);
    fireEvent.click(screen.getByRole('button', { name: /SQL/ }));
    expect(screen.getByTestId('sql-masked')).toBeInTheDocument();
  });
  it('the View SQL disclosure under a table shows it', () => {
    render(<AssistantTurn msg={viewParts([table, query({ maskedLiterals: 2 })])} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    fireEvent.click(screen.getByRole('button', { name: /View SQL/i }));
    expect(screen.getAllByTestId('sql-masked').length).toBeGreaterThan(0);
  });
  it('StepTimeline SQL detail and the chosen candidate show it', () => {
    render(<StepTimeline steps={[
      { id: 's', kind: 'sql', state: 'ok', label: 'Ran query', sql: MASKED, maskedLiterals: 2 },
      { id: 'c', kind: 'candidates', state: 'ok', label: 'Compared 2 candidates', candidates: [{ id: 'a', label: 'a', sql: MASKED, maskedLiterals: 1, chosen: true, ok: true }, { id: 'b', label: 'b', sql: 'SELECT 1', ok: true }] },
    ]} live={false} />);
    for (let i = 0; i < 3; i++) for (const b of screen.queryAllByRole('button', { expanded: false })) fireEvent.click(b);
    expect(screen.getAllByTestId('sql-masked')).toHaveLength(1); // the sql step
    fireEvent.click(screen.getByRole('button', { name: 'a' }));
    expect(screen.getAllByTestId('sql-masked')).toHaveLength(2); // + the shown candidate
  });
});

describe('masked SQL is never offered as runnable', () => {
  it('no Run / Re-run / Execute action anywhere in a turn with masked or rerunnable SQL', () => {
    render(<AssistantTurn msg={viewParts([table, query({ maskedLiterals: 2, rerunnable: true })])} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    fireEvent.click(screen.getByRole('button', { name: /View SQL/i }));
    const names = screen.getAllByRole('button').map((b) => b.getAttribute('aria-label') ?? b.textContent ?? '');
    expect(names.filter((n) => /\b(re-?run|run|execute)\b/i.test(n))).toEqual([]);
  });
  it('copied masked SQL is labelled as not runnable as-is', () => {
    expect(sqlForClipboard([{ label: 'q', sql: MASKED, maskedLiterals: 2 }])).toMatch(/^-- Patient values hidden: 2 values are shown as \?\. Not runnable as-is\.\nSELECT/);
    expect(sqlForClipboard([{ label: 'q', sql: 'SELECT 1' }])).toBe('SELECT 1');
    const two = sqlForClipboard([{ label: 'a', sql: MASKED, maskedLiterals: 1 }, { label: 'b', sql: 'SELECT 1' }]);
    expect(two).toContain('-- 1. a\n-- Patient values hidden: 1 value is shown as ?.');
    expect(two).toContain('-- 2. b\nSELECT 1;');
  });
});
