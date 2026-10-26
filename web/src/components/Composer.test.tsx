import { describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach } from 'vitest';
afterEach(cleanup);
import { Composer, isLongPaste } from './Composer';

const long = Array.from({ length: 30 }, (_, i) => `row ${i}, claim C-${1000 + i}, CO-18`).join('\n');
const paste = (el: HTMLElement, text: string) =>
  fireEvent.paste(el, { clipboardData: { getData: (k: string) => (k === 'text/plain' ? text : '') } });

describe('composer long paste', () => {
  it('detects long pastes by size or line count', () => {
    expect(isLongPaste('short')).toBe(false);
    expect(isLongPaste(long)).toBe(true);
    expect(isLongPaste('x'.repeat(1300))).toBe(true);
  });
  it('turns a long paste into a chip, sends it after the typed text, and Backspace removes it', () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} onStop={() => {}} busy={false} />);
    const box = screen.getByRole('textbox', { name: 'Message' });
    paste(box, 'short text');
    expect(screen.queryByTestId('paste-chip')).toBeNull();
    paste(box, long);
    expect(screen.getAllByTestId('paste-chip')).toHaveLength(1);
    expect((box as HTMLTextAreaElement).value).toBe('');
    expect(screen.getByRole('button', { name: 'Send message' })).not.toBeDisabled();
    fireEvent.keyDown(box, { key: 'Backspace' });
    expect(screen.queryByTestId('paste-chip')).toBeNull();
    paste(box, long);
    fireEvent.change(box, { target: { value: 'Summarize these rows' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith(`Summarize these rows\n\n${long}`);
    expect(screen.queryByTestId('paste-chip')).toBeNull();
  });
  it('removes a chip with its button', () => {
    render(<Composer onSend={() => {}} onStop={() => {}} busy={false} />);
    paste(screen.getByRole('textbox', { name: 'Message' }), long);
    fireEvent.click(screen.getByRole('button', { name: /Remove pasted text \(30 lines\)/ }));
    expect(screen.queryByTestId('paste-chip')).toBeNull();
  });
});
