/** Action row / copy feedback / version arrows, matched to ChatGPT/Claude behaviour (10:48 PM directive). */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { COPIED_MS, CopyButton } from '../ui';
import { AssistantTurn } from '../Message';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent } from '../../api/contract';

const write = vi.fn<(t: string) => Promise<void>>();
beforeEach(() => {
  vi.useFakeTimers();
  write.mockReset().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: write }, configurable: true });
});
afterEach(() => { vi.useRealTimers(); cleanup(); });
const flush = () => act(async () => { await Promise.resolve(); await Promise.resolve(); });

describe('copy feedback', () => {
  it('swaps to a check and "Copied" in place, announces it politely, and reverts after ~2 s', async () => {
    render(<CopyButton text="x" tip />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    await flush();
    expect(screen.getByRole('button', { name: 'Copied' })).toHaveAttribute('data-state', 'done');
    expect(screen.getByTestId('copy-status')).toHaveTextContent('Copied');
    expect(screen.getByTestId('copy-status')).toHaveAttribute('aria-live', 'polite');
    act(() => { vi.advanceTimersByTime(COPIED_MS - 100); });
    expect(screen.getByRole('button', { name: 'Copied' })).toBeTruthy();
    act(() => { vi.advanceTimersByTime(200); });
    expect(screen.getByRole('button', { name: 'Copy' })).toBeTruthy();
    expect(screen.getByTestId('copy-status').textContent).toBe('');
  });
  it('a second click restarts the timer instead of flickering back early', async () => {
    render(<CopyButton text="x" />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    await flush();
    act(() => { vi.advanceTimersByTime(COPIED_MS - 200); });
    fireEvent.click(screen.getByRole('button', { name: 'Copied' }));
    await flush();
    act(() => { vi.advanceTimersByTime(400); });
    expect(screen.getByRole('button', { name: 'Copied' })).toBeTruthy();
  });
  it('a refused clipboard says "Couldn\'t copy" rather than pretending', async () => {
    write.mockRejectedValue(new Error('denied'));
    render(<CopyButton text="x" label="Copy code" withText />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy code' }));
    await flush();
    const b = screen.getByRole('button', { name: "Couldn't copy" });
    expect(b).toHaveAttribute('data-state', 'failed');
    expect(b.textContent).not.toMatch(/Copied/);
  });
  it('unmounting while "Copied" shows leaves no pending timer', async () => {
    const { unmount } = render(<CopyButton text="x" />);
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    await flush();
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});

const view = () => [
  { type: 'message.start', messageId: 'a1', conversationId: 'c1' },
  { type: 'part', part: { type: 'text', id: 't1', text: 'Denial rate is 12%.' } },
  { type: 'message.end', messageId: 'a1', finishReason: 'stop', usage: { queries: 1, elapsedMs: 10 } },
].reduce((m, e) => reduceV1(m, e as StreamEvent), newAssistantViewV1('a1') as AssistantViewV1);

describe('version arrows ‹ 1/2 ›', () => {
  it('a named group, arrows labelled, count readable as "1 of 2", ends disabled', () => {
    const onVersion = vi.fn();
    render(<AssistantTurn msg={view()} isLast onOpen={() => {}} onRegenerate={() => {}} onEdit={() => {}} version={{ current: 1, total: 2 }} onVersion={onVersion} />);
    const g = screen.getByRole('group', { name: 'Versions' });
    expect(g).toHaveTextContent('1/2');
    expect(within(g).getByText('1 of 2')).toHaveClass('sr-only');
    expect(within(g).getByRole('button', { name: 'Previous version' })).toBeDisabled();
    fireEvent.click(within(g).getByRole('button', { name: 'Next version' }));
    expect(onVersion).toHaveBeenCalledWith(1);
  });
  it('sits first in the action row, before Copy (ChatGPT/Claude order)', () => {
    render(<AssistantTurn msg={view()} isLast onOpen={() => {}} onRegenerate={() => {}} onEdit={() => {}} version={{ current: 2, total: 2 }} onVersion={() => {}} />);
    const row = screen.getByTestId('msg-actions');
    expect(row.firstElementChild).toBe(screen.getByTestId('version-nav'));
  });
});
