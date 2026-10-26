/** Clock skew: server deadlines (expiresAt, Retry-After dates) are judged by the server clock, not a skewed local one. */
import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { noteServerDate, resetServerClock, serverNow, serverSkewMs } from './serverClock';
import { useExpired } from './useExpired';
import { httpError, retryAfterMs } from '../adapters/http';

const LOCAL = Date.parse('2026-10-25T12:00:00Z');

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(LOCAL); resetServerClock(); });
afterEach(() => { vi.useRealTimers(); resetServerClock(); });

describe('serverClock', () => {
  it('tracks a skew past 2 s and ignores 1-s jitter and garbled headers', () => {
    noteServerDate('Sun, 25 Oct 2026 12:00:01 GMT');
    expect(serverSkewMs()).toBe(0);
    noteServerDate('Sun, 25 Oct 2026 12:10:00 GMT'); // server 10 min ahead
    expect(serverSkewMs()).toBe(600_000);
    expect(serverNow()).toBe(LOCAL + 600_000);
    noteServerDate('nonsense'); noteServerDate(null);
    expect(serverSkewMs()).toBe(600_000);
    noteServerDate('Sun, 25 Oct 2026 12:00:00 GMT'); // clocks agree again
    expect(serverSkewMs()).toBe(0);
  });
});

describe('Retry-After HTTP date under skew', () => {
  it('is measured against the response Date, whatever the local clock says', () => {
    // Local clock is an hour slow: against it, "5 s" would read as 1 h 5 s and fall through to the error card.
    expect(retryAfterMs('Sun, 25 Oct 2026 13:00:05 GMT', LOCAL, 'Sun, 25 Oct 2026 13:00:00 GMT')).toBe(5000);
    // Local clock is an hour fast: against it the date is in the past and we'd retry at once.
    expect(retryAfterMs('Sun, 25 Oct 2026 11:00:05 GMT', LOCAL, 'Sun, 25 Oct 2026 11:00:00 GMT')).toBe(5000);
  });

  it('falls back to the server clock estimate when the response has no Date', () => {
    noteServerDate('Sun, 25 Oct 2026 13:00:00 GMT');
    expect(retryAfterMs('Sun, 25 Oct 2026 13:00:05 GMT')).toBe(5000);
  });

  it('rejects numbers that are not delay-seconds instead of guessing a date', () => {
    expect(retryAfterMs('1.5')).toBeNull();
    expect(retryAfterMs('-3')).toBeNull();
    expect(retryAfterMs('120')).toBe(120_000);
  });

  it('httpError reports the wait from the response clock', async () => {
    const res = new Response(JSON.stringify({ error: { code: 'rate_limited', message: 'slow down' } }), {
      status: 429,
      headers: { 'content-type': 'application/json', date: 'Sun, 25 Oct 2026 14:00:00 GMT', 'retry-after': 'Sun, 25 Oct 2026 14:00:07 GMT' },
    });
    const part = await httpError(res);
    expect(part.retryAfterMs).toBe(7000);
  });
});

describe('useExpired under skew', () => {
  it('does not expire a card early when the local clock runs fast', () => {
    vi.setSystemTime(LOCAL + 10 * 60_000); // local clock 10 min fast
    noteServerDate('Sun, 25 Oct 2026 12:00:00 GMT');
    const { result } = renderHook(() => useExpired('2026-10-25T12:05:00Z')); // 5 min left in server time
    expect(result.current).toBe(false);
    act(() => { vi.advanceTimersByTime(4 * 60_000); });
    expect(result.current).toBe(false);
    act(() => { vi.advanceTimersByTime(61_000); });
    expect(result.current).toBe(true);
  });

  it('expires on time when the local clock runs slow', () => {
    noteServerDate('Sun, 25 Oct 2026 12:30:00 GMT'); // server 30 min ahead
    const { result } = renderHook(() => useExpired('2026-10-25T12:10:00Z'));
    expect(result.current).toBe(true);
  });
});
