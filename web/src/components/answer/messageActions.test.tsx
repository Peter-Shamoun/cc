import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AssistantTurn, REFINE_PROMPT } from '../Message';
import { DUP_V1_ANSWER, DUP_V1_STEPS } from '../../fixtures/answerV1';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent as V1Event } from '../../api/contract';
import { clearFeedbackForTests, feedbackFor, recordFeedback, setFeedbackSink, sqlForClipboard } from '../../lib/feedback';

const v1 = (evs: V1Event[]) => evs.reduce((m, e) => reduceV1(m, e), newAssistantViewV1('a1') as AssistantViewV1);
const noop = () => {};
let clip: string[] = [];
beforeEach(() => {
  clip = [];
  Object.assign(navigator, { clipboard: { writeText: vi.fn(async (t: string) => { clip.push(t); }) } });
});
afterEach(() => { cleanup(); clearFeedbackForTests(); });

describe('feedback store (memory only)', () => {
  it('keeps the vote per message, drops reasons on a new vote, and calls the sink', () => {
    const sink = vi.fn();
    setFeedbackSink(sink);
    recordFeedback({ messageId: 'm1', vote: 'down', reasons: ['Wrong numbers'], detail: 'x' });
    expect(feedbackFor('m1')).toMatchObject({ vote: 'down', reasons: ['Wrong numbers'] });
    recordFeedback({ messageId: 'm1', vote: 'up' });
    expect(feedbackFor('m1')?.reasons).toBeUndefined();
    expect(sink).toHaveBeenCalledTimes(2);
    expect(localStorage.length).toBe(0);
  });
  it('sqlForClipboard: one query as-is, several numbered, failed ones skipped', () => {
    expect(sqlForClipboard([{ label: 'Run query', sql: 'select 1' }])).toBe('select 1');
    expect(sqlForClipboard([{ label: 'Bad', sql: 'selec', state: 'error' }, { label: 'Count', sql: 'select 1' }, { label: 'Rate', sql: 'select 2;' }]))
      .toBe('-- 1. Count\nselect 1;\n\n-- 2. Rate\nselect 2;');
  });
});

describe('message actions', () => {
  const view = () => v1([...DUP_V1_STEPS, ...DUP_V1_ANSWER]);
  it('regenerate menu: Try again regenerates, More concise / More detailed refine', () => {
    const regen = vi.fn();
    const refine = vi.fn();
    render(<AssistantTurn msg={view()} isLast onOpen={noop} onRegenerate={regen} onEdit={noop} onRefine={refine} />);
    fireEvent.click(screen.getByTestId('regenerate-menu-button'));
    expect(screen.getByRole('menu', { name: 'Regenerate' })).toBeTruthy();
    fireEvent.click(screen.getByRole('menuitem', { name: /More concise/ }));
    expect(refine).toHaveBeenCalledWith('concise');
    fireEvent.click(screen.getByTestId('regenerate-menu-button'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Try again/ }));
    expect(regen).toHaveBeenCalledTimes(1);
    expect(REFINE_PROMPT.detailed).toMatch(/more detail/);
  });
  it('without onRefine the button regenerates directly', () => {
    const regen = vi.fn();
    render(<AssistantTurn msg={view()} isLast onOpen={noop} onRegenerate={regen} onEdit={noop} />);
    fireEvent.click(screen.getByRole('button', { name: 'Regenerate' }));
    expect(regen).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('menu')).toBeNull();
  });
  it('More actions: Copy SQL and Copy as Markdown', async () => {
    render(<AssistantTurn msg={view()} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    fireEvent.click(screen.getByTestId('more-actions'));
    fireEvent.click(screen.getByRole('menuitem', { name: /Copy SQL/ }));
    await vi.waitFor(() => expect(clip.length).toBe(1));
    expect(clip[0].toLowerCase()).toContain('select');
    fireEvent.click(screen.getByTestId('more-actions'));
    fireEvent.click(screen.getByRole('menuitem', { name: 'Copy as Markdown' }));
    await vi.waitFor(() => expect(clip.length).toBe(2));
    expect(clip[1]).toContain('**Lakeside Family Medicine**');
  });
  it('thumbs survive a remount (memory store) and a down vote with reasons is recorded', () => {
    const { unmount } = render(<AssistantTurn msg={view()} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    fireEvent.click(screen.getByRole('button', { name: 'Bad response' }));
    fireEvent.click(screen.getByRole('button', { name: 'Wrong numbers' }));
    fireEvent.click(screen.getByRole('button', { name: 'Submit' }));
    expect(feedbackFor(view().id)).toMatchObject({ vote: 'down', reasons: ['Wrong numbers'] });
    unmount();
    render(<AssistantTurn msg={view()} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.getByRole('button', { name: 'Bad response' }).getAttribute('aria-pressed')).toBe('true');
  });
});
