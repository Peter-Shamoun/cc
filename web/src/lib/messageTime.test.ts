import { describe, expect, it } from 'vitest';
import { messageTime } from './messageTime';

const now = new Date(2026, 9, 25, 22, 30);
describe('messageTime (QA-S23)', () => {
  it('today: time only', () => {
    expect(messageTime(new Date(2026, 9, 25, 9, 5).toISOString(), now, 'en-US')?.label).toBe('9:05 AM');
  });
  it('this year: month, day and time', () => {
    expect(messageTime(new Date(2026, 9, 24, 21, 0).toISOString(), now, 'en-US')?.label).toBe('Oct 24, 9:00 PM');
  });
  it('older: date with year, no time', () => {
    expect(messageTime(new Date(2025, 2, 3, 8, 0).toISOString(), now, 'en-US')?.label).toBe('Mar 3, 2025');
  });
  it('tooltip has the full date', () => {
    expect(messageTime(new Date(2026, 9, 24, 21, 0).toISOString(), now, 'en-US')?.full).toMatch(/Saturday, October 24, 2026/);
  });
  it('missing or invalid input gives null', () => {
    expect(messageTime(undefined, now)).toBeNull();
    expect(messageTime('garbage', now)).toBeNull();
  });
});
