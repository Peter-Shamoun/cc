import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { UserBubble, isLongQuestion } from './Message';

afterEach(cleanup);
const msg = (text: string) => ({ id: 'u1', role: 'user' as const, text, createdAt: '2026-01-01T00:00:00Z' });

describe('UserBubble long questions', () => {
  it('detects long pastes by length or line count', () => {
    expect(isLongQuestion('Denials by payer last quarter?')).toBe(false);
    expect(isLongQuestion('x'.repeat(701))).toBe(true);
    expect(isLongQuestion(Array.from({ length: 12 }, (_, i) => `line ${i}`).join('\n'))).toBe(true);
  });
  it('short questions have no Show more', () => {
    render(<UserBubble msg={msg('Top denial reasons?')} />);
    expect(screen.queryByTestId('user-show-more')).toBeNull();
  });
  it('long questions collapse and toggle with Show more / Show less', () => {
    render(<UserBubble msg={msg(Array.from({ length: 30 }, (_, i) => `CLM-${i},PAYER-${i % 3},CO-45`).join('\n'))} />);
    const btn = screen.getByTestId('user-show-more');
    expect(btn.textContent).toBe('Show more');
    expect(btn.getAttribute('aria-expanded')).toBe('false');
    expect(screen.getByTestId('user-text').className).toContain('max-h-');
    fireEvent.click(btn);
    expect(btn.textContent).toBe('Show less');
    expect(btn.getAttribute('aria-expanded')).toBe('true');
    expect(screen.getByTestId('user-text').className).not.toContain('max-h-');
  });
  it('hover actions overlay instead of reserving a row (no gap under the bubble)', () => {
    render(<UserBubble msg={msg('Top denial reasons?')} onEdit={() => {}} />);
    expect(screen.getByTestId('user-actions').className).toContain('absolute');
  });
  it('version arrows keep the row in flow', () => {
    render(<UserBubble msg={msg('Q')} onEdit={() => {}} version={{ current: 1, total: 2 }} onVersion={() => {}} />);
    expect(screen.getByTestId('user-actions').className).not.toContain('absolute');
  });
});

describe('UserBubble send time (QA-S23)', () => {
  it('shows a muted <time> in the hover actions with the full date as tooltip', () => {
    render(<UserBubble msg={msg('Top denial reasons?')} onEdit={() => {}} />);
    const t = screen.getByTestId('msg-time');
    expect(t.tagName).toBe('TIME');
    expect(t.getAttribute('datetime')).toBe('2026-01-01T00:00:00Z');
    expect(t.getAttribute('title')).toMatch(/2025|2026/);
    expect(screen.getByTestId('user-actions').contains(t)).toBe(true);
    expect(t.className).toContain('text-fg-subtle');
  });
  it('renders nothing for a missing or invalid createdAt', () => {
    render(<UserBubble msg={{ ...msg('Q'), createdAt: 'nope' }} />);
    expect(screen.queryByTestId('msg-time')).toBeNull();
  });
});
