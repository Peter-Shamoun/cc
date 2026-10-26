import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { AssistantTurn } from '../Message';
import { DUP_V1_ANSWER, DUP_V1_STEPS } from '../../fixtures/answerV1';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent as V1Event } from '../../api/contract';
import { isFollowUps } from './FollowUps';

vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));
afterEach(cleanup);
const noop = () => {};
const v1 = (evs: V1Event[]) => evs.reduce((m, e) => reduceV1(m, e), newAssistantViewV1('a1') as AssistantViewV1);
const FU = (items: { id: string; text: string }[]) => ({ type: 'part', part: { type: 'follow_ups', id: 'fu1', items } }) as unknown as V1Event;
const ITEMS = [
  { id: 'f1', text: 'Break the 9.0% down by payer' },
  { id: 'f2', text: 'Compare with Q2 2026' },
  { id: 'f3', text: 'Which claim types drive the duplicates?' },
  { id: 'f4', text: 'A fourth one that should be dropped' },
];
// The follow_ups part arrives before message.end (as the demo would send it).
const withFU = (items = ITEMS, end = true) => {
  const ans = DUP_V1_ANSWER as V1Event[];
  const body = ans.slice(0, -1);
  return v1([...(DUP_V1_STEPS as V1Event[]), ...body, FU(items), ...(end ? [ans[ans.length - 1]] : [])]);
};

describe('suggested follow-ups', () => {
  it('shows at most 3 plain rows under the last answer, and a click sends that text', () => {
    const send = vi.fn();
    render(<AssistantTurn msg={withFU()} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={send} />);
    const rows = screen.getAllByTestId('follow-up');
    expect(rows.map((r) => r.textContent)).toEqual(ITEMS.slice(0, 3).map((i) => i.text));
    expect(screen.getByRole('navigation', { name: 'Suggested follow-ups' })).toBeTruthy();
    fireEvent.click(rows[1]);
    expect(send).toHaveBeenCalledWith('Compare with Q2 2026');
    expect(screen.queryByText(/Unsupported content/i)).toBeNull();
  });
  it('is quiet: no pills, no sparkle/emoji, rows sit after the action row', () => {
    render(<AssistantTurn msg={withFU()} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={noop} />);
    const nav = screen.getByTestId('follow-ups');
    expect(nav.innerHTML).not.toMatch(/rounded-full|shadow|gradient|sparkle/);
    expect(nav.textContent).not.toMatch(/\p{Extended_Pictographic}/u);
    const actions = screen.getByTestId('msg-actions');
    expect(actions.compareDocumentPosition(nav) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
  it('hidden on older turns, while streaming, on read-only views and without onSend', () => {
    const { rerender } = render(<AssistantTurn msg={withFU()} isLast={false} onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={noop} />);
    expect(screen.queryByTestId('follow-ups')).toBeNull();
    rerender(<AssistantTurn msg={withFU(ITEMS, false)} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={noop} />);
    expect(screen.queryByTestId('follow-ups')).toBeNull();
    rerender(<AssistantTurn msg={withFU()} isLast readOnly onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={noop} />);
    expect(screen.queryByTestId('follow-ups')).toBeNull();
    rerender(<AssistantTurn msg={withFU()} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect(screen.queryByTestId('follow-ups')).toBeNull();
  });
  it('blank items are dropped; an all-blank list renders nothing; malformed parts are not follow-ups', () => {
    render(<AssistantTurn msg={withFU([{ id: 'a', text: '  ' }, { id: 'b', text: '' }])} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={noop} />);
    expect(screen.queryByTestId('follow-ups')).toBeNull();
    expect(isFollowUps({ type: 'follow_ups' })).toBe(false);
    expect(isFollowUps({ type: 'text' })).toBe(false);
  });
  it('long suggestions truncate on one line (no wrapping walls of text)', () => {
    render(<AssistantTurn msg={withFU([{ id: 'l', text: 'x'.repeat(400) }])} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onSend={noop} />);
    expect(screen.getByTestId('follow-up').querySelector('.truncate')).toBeTruthy();
  });
});

describe('Ask to change response…', () => {
  const turn = (send = vi.fn()) => { render(<AssistantTurn msg={withFU([])} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onRefine={noop} onSend={send} />); return send; };
  const openIt = () => { fireEvent.click(screen.getByTestId('regenerate-menu-button')); fireEvent.click(screen.getByRole('menuitem', { name: /Ask to change response/ })); return screen.getByRole('textbox', { name: 'Ask to change response' }); };
  it('menu item opens a focused input; Enter sends the instruction and closes it', () => {
    const send = turn();
    const input = openIt();
    expect(document.activeElement).toBe(input);
    fireEvent.change(input, { target: { value: '  Use a table instead  ' } });
    fireEvent.submit(input.closest('form')!);
    expect(send).toHaveBeenCalledWith('Use a table instead');
    expect(screen.queryByTestId('change-response')).toBeNull();
  });
  it('empty submit does nothing; Escape closes and returns focus to the menu button', () => {
    const send = turn();
    const input = openIt();
    fireEvent.submit(input.closest('form')!);
    expect(send).not.toHaveBeenCalled();
    fireEvent.keyDown(input, { key: 'Escape' });
    expect(screen.queryByTestId('change-response')).toBeNull();
    expect(document.activeElement).toBe(screen.getByTestId('regenerate-menu-button'));
  });
  it('blurring an empty input closes it; text is capped at 500 chars', () => {
    turn();
    const input = openIt();
    expect(input.getAttribute('maxlength')).toBe('500');
    fireEvent.blur(input);
    expect(screen.queryByTestId('change-response')).toBeNull();
  });
  it('not offered without onSend (shared view)', () => {
    render(<AssistantTurn msg={withFU([])} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} onRefine={noop} />);
    fireEvent.click(screen.getByTestId('regenerate-menu-button'));
    expect(screen.queryByRole('menuitem', { name: /Ask to change response/ })).toBeNull();
  });
});
