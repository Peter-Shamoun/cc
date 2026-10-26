import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { AssistantTurn } from '../Message';
import { StepTimeline } from './StepTimeline';
import { DUP_V1_ANSWER, DUP_V1_STEPS } from '../../fixtures/answerV1';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent as V1Event } from '../../api/contract';

vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));
afterEach(cleanup);
const noop = () => {};
const v1 = (evs: V1Event[]) => evs.reduce((m, e) => reduceV1(m, e), newAssistantViewV1('a1') as AssistantViewV1);
const turn = (m: AssistantViewV1) => <AssistantTurn msg={m} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />;

describe('QA-A15: one Thinking row from turn start', () => {
  it('shows the Thinking row (icon + timer) before any event, never a bare caret', () => {
    render(turn(newAssistantViewV1('a1') as AssistantViewV1));
    const row = screen.getByTestId('step-summary');
    expect(row.textContent).toMatch(/^Thinking…\d/);
    expect(row.querySelector('svg.animate-spin')).not.toBeNull();
    expect(document.querySelector('.streaming-caret')).toBeNull();
    expect(screen.queryByTestId('status')).toBeNull();
    expect(screen.queryAllByTestId('step')).toHaveLength(0);
  });
  it('keeps the same row element as steps arrive and folds into "Thought for" when the answer lands', () => {
    const { rerender } = render(turn(newAssistantViewV1('a1') as AssistantViewV1));
    const row = screen.getByTestId('step-summary');
    rerender(turn(v1(DUP_V1_STEPS.slice(0, 4))));
    expect(screen.getByTestId('step-summary')).toBe(row);
    expect(screen.getAllByTestId('step').length).toBeGreaterThan(0);
    rerender(turn(v1([...DUP_V1_STEPS, ...DUP_V1_ANSWER])));
    expect(screen.getByTestId('step-summary')).toBe(row);
    expect(row.textContent).toMatch(/^Thought for/);
  });
  it('transport states (offline, 429) still add their own line under the row', () => {
    render(turn({ ...(newAssistantViewV1('a1') as AssistantViewV1), status: 'Reconnecting…' }));
    expect(screen.getByTestId('step-summary')).toBeInTheDocument();
    expect(screen.getByTestId('status')).toHaveTextContent('Reconnecting…');
  });
});

describe('QA-A16: Thought for ≥ the longest step', () => {
  it('replayed answers never show a total below a single step', () => {
    render(<StepTimeline live={false} elapsedMs={1800} steps={[{ id: 's1', kind: 'sql', label: 'Query claims', state: 'ok', durationMs: 2430 }]} />);
    expect(screen.getByTestId('step-summary').textContent).toMatch(/^Thought for 2\.4s/);
  });
});
