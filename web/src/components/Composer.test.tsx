import { describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach } from 'vitest';
afterEach(cleanup);
import { Composer, isLongPaste, ATTACH_MAX } from './Composer';
import { waitFor } from '@testing-library/react';

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

describe('composer text-file attachments', () => {
  it('attaches text files as chips, skips binaries and big files, and sends name + content', async () => {
    const onSend = vi.fn();
    render(<Composer onSend={onSend} onStop={() => {}} busy={false} />);
    const input = screen.getByTestId('attach-input') as HTMLInputElement;
    const csv = new File(['claim,carc\nC-1,CO-18\n'], 'rows.csv', { type: 'text/csv' });
    const png = new File([new Uint8Array([137, 80])], 'scan.png', { type: 'image/png' });
    const big = new File(['x'.repeat(ATTACH_MAX + 1)], 'big.txt', { type: 'text/plain' });
    fireEvent.change(input, { target: { files: [csv, png, big] } });
    await waitFor(() => expect(screen.getAllByTestId('paste-chip')).toHaveLength(1));
    expect(screen.getByText('rows.csv')).toBeInTheDocument();
    expect(screen.getByTestId('attach-notice').textContent).toBe("scan.png isn't a text file · big.txt is over 256 KB");
    const box = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(box, { target: { value: 'Group by CARC' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    expect(onSend).toHaveBeenCalledWith('Group by CARC\n\nrows.csv:\nclaim,carc\nC-1,CO-18\n');
    expect(screen.queryByTestId('attach-notice')).toBeNull();
  });
});


describe('scope popover', () => {
  it('opens from the Claims lake chip, shows the locked raw scope and closes on Esc back to the chip', async () => {
    render(<Composer onSend={() => {}} onStop={() => {}} busy={false} />);
    const chip = screen.getByTestId('scope-chip');
    expect(chip).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(chip);
    const pop = screen.getByRole('dialog', { name: 'Data scope' });
    expect(chip).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getByRole('radio', { name: /Claims lake/ })).toHaveAttribute('aria-checked', 'true');
    expect(screen.getByRole('radio', { name: /Raw EDI/ })).toHaveAttribute('aria-disabled', 'true');
    expect(pop).toHaveTextContent('sem.claim_outcomes');
    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole('radio', { name: /Claims lake/ })));
    fireEvent.keyDown(pop, { key: 'Escape' });
    expect(screen.queryByTestId('scope-popover')).toBeNull();
    expect(document.activeElement).toBe(chip);
  });
  it('closes on an outside click', () => {
    render(<Composer onSend={() => {}} onStop={() => {}} busy={false} />);
    fireEvent.click(screen.getByTestId('scope-chip'));
    fireEvent.mouseDown(document.body);
    expect(screen.queryByTestId('scope-popover')).toBeNull();
  });
});
