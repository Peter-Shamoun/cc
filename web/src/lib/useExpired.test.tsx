import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useExpired } from './useExpired';

describe('useExpired', () => {
  beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-10-25T12:00:00Z')); });
  afterEach(() => vi.useRealTimers());

  it('is false with no deadline or an invalid one', () => {
    expect(renderHook(() => useExpired(undefined)).result.current).toBe(false);
    expect(renderHook(() => useExpired('not a date')).result.current).toBe(false);
  });
  it('is true at once for a past deadline', () => {
    expect(renderHook(() => useExpired('2026-10-25T11:59:00Z')).result.current).toBe(true);
  });
  it('flips to true when the deadline passes while mounted', () => {
    const { result } = renderHook(() => useExpired('2026-10-25T12:00:05Z'));
    expect(result.current).toBe(false);
    act(() => { vi.advanceTimersByTime(5_001); });
    expect(result.current).toBe(true);
  });
  it('a renewed (later) deadline is not expired until it passes', () => {
    const { result, rerender } = renderHook(({ at }) => useExpired(at), { initialProps: { at: '2026-10-25T11:00:00Z' } });
    expect(result.current).toBe(true);
    rerender({ at: '2026-10-25T13:00:00Z' });
    expect(result.current).toBe(false);
  });
});
