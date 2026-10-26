import { afterEach, describe, expect, it } from 'vitest';
import { useState } from 'react';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AssistantTurn, UserBubble } from '../Message';
import { StepTimeline } from './StepTimeline';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent } from '../../api/contract';
import type { UserMessage } from '../../lib/contract';

afterEach(cleanup);
const noop = () => {};
const frames = () => act(() => new Promise<void>((r) => requestAnimationFrame(() => requestAnimationFrame(() => r()))));
const view = (parts: unknown[]) => [
  { type: 'message.start', messageId: 'a1', conversationId: 'c1' },
  ...parts.map((part) => ({ type: 'part', part })),
  { type: 'message.end', messageId: 'a1', finishReason: 'error', usage: { queries: 0, elapsedMs: 10 } },
].reduce((m, e) => reduceV1(m, e as StreamEvent), newAssistantViewV1('a1') as AssistantViewV1);
const user = (id: string, text: string) => ({ id, role: 'user', text } as unknown as UserMessage);

describe('QA-A21: focus after edit and version switch', () => {
  it('Esc and Cancel return focus to the Edit button', () => {
    render(<UserBubble msg={user('u1', 'Total paid by payer')} onEdit={noop} />);
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }));
    const box = screen.getByRole('textbox', { name: 'Edit message' });
    fireEvent.keyDown(box, { key: 'Escape' });
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Edit message' }));
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }));
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Edit message' }));
  });

  it('a real edit sends focus to the composer', async () => {
    render(<div><div data-message-id="u1"><UserBubble msg={user('u1', 'Total paid')} onEdit={noop} /></div><textarea aria-label="Message" /></div>);
    fireEvent.click(screen.getByRole('button', { name: 'Edit message' }));
    fireEvent.change(screen.getByRole('textbox', { name: 'Edit message' }), { target: { value: 'Total paid by month' } });
    fireEvent.keyDown(screen.getByRole('textbox', { name: 'Edit message' }), { key: 'Enter' });
    await frames();
    expect(document.activeElement).toBe(screen.getByRole('textbox', { name: 'Message' }));
  });

  it('switching versions keeps focus on the arrow in the remounted row', async () => {
    function Thread() {
      const [k, setK] = useState(1);
      const m = user(`u${k}`, `Question v${k + 1}`);
      return <div key={m.id} data-message-id={m.id}><UserBubble msg={m} onEdit={noop} version={{ current: k + 1, total: 3 }} onVersion={setK} /></div>;
    }
    render(<Thread />);
    const prev = screen.getByRole('button', { name: 'Previous version' });
    prev.focus();
    fireEvent.click(prev);
    await frames();
    expect(screen.getByText('Question v1')).toBeTruthy();
    // At 1/3 "Previous" is disabled, so focus moves to "Next" rather than <body>.
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Next version' }));
    fireEvent.click(screen.getByRole('button', { name: 'Next version' }));
    await frames();
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Next version' }));
  });
});

describe('QA-A22: failed turns', () => {
  const err = { type: 'error', id: 'e1', code: 'timeout', title: 'Query timed out', message: 'Try again.', retryable: true };
  it('shows the card Retry plus Copy only: no second regenerate, feedback or style menu', () => {
    render(<AssistantTurn msg={view([err])} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onRefine={noop} />);
    expect(screen.getAllByRole('button', { name: /Retry|Regenerate/ })).toHaveLength(1);
    expect(screen.getByRole('button', { name: /Retry/ })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Copy/ })).toBeTruthy();
    expect(screen.queryByTestId('sources-chip')).toBeNull();
    for (const name of ['Good response', 'Bad response', 'Answer style', 'More actions']) expect(screen.queryByRole('button', { name })).toBeNull();
  });
  it('a non-retryable failure keeps one Regenerate in the bar', () => {
    render(<AssistantTurn msg={view([{ ...err, retryable: false }])} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.getAllByRole('button', { name: /Retry|Regenerate/ }).map((b) => b.getAttribute('aria-label') ?? b.textContent)).toEqual(['Regenerate']);
  });
  it('step summary name has separators', () => {
    const steps = [
      { id: 's1', kind: 'sql', state: 'error', title: 'Query', startedAt: 0, endedAt: 900 },
      { id: 's2', kind: 'repair', state: 'ok', title: 'Repair', startedAt: 900, endedAt: 1000 },
    ];
    render(<StepTimeline steps={[steps[0]] as never} live={false} elapsedMs={1000} reasoning={[]} />);
    expect(screen.getByTestId('step-summary').textContent).toMatch(/Thought for 1(\.0)?s, 1 failed/);
    cleanup();
    render(<StepTimeline steps={[steps[1]] as never} live={false} elapsedMs={1000} reasoning={[]} />);
    expect(screen.getByTestId('step-summary').textContent).toMatch(/s, all failures repaired/);
  });
});
