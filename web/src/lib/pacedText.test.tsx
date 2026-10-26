import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, render, screen } from '@testing-library/react';
import { DRAIN_MS, nextReveal, usePacedText } from './pacedText';

const frames = (text: string, from = 0) => { let n = from, f = 0; while (n < text.length && f < 1000) { n = nextReveal(n, text, 16); f++; } return f; };
const para = 'Cigna has the highest denial rate at 20.4%, followed by Medicaid at 20.3%. '.repeat(8); // ~600 chars

describe('nextReveal (streaming pacing)', () => {
  it('a 600-char burst eases out in under a second, not in one jump and not as a slow crawl', () => {
    expect(nextReveal(0, para, 16)).toBeLessThan(para.length / 4);
    const f = frames(para);
    expect(f * 16).toBeLessThan(1000);
    expect(f).toBeGreaterThan(DRAIN_MS / 16);
  });
  it('a short tail is quick (>= 250 chars/s)', () => {
    expect(frames('Paid amount by payer.') * 16).toBeLessThanOrEqual(100);
  });
  it('never stops mid-word when the word ends soon', () => {
    for (let n = 0; n < para.length; n = nextReveal(n, para, 16)) {
      const next = nextReveal(n, para, 16);
      if (next < para.length) expect(para[next]).toMatch(/\s/);
    }
  });
  it('monotonic and capped at the target', () => {
    expect(nextReveal(10, 'short', 16)).toBe(5);
    expect(nextReveal(5, 'short', 16)).toBe(5);
  });
});

function Probe({ text, streaming }: { text: string; streaming: boolean }) {
  const { shown, pending } = usePacedText(text, streaming);
  return <p data-testid="p" data-pending={pending || undefined}>{shown}</p>;
}

describe('usePacedText', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); cleanup(); });

  it('history (not streaming at mount) shows at once', () => {
    render(<Probe text={para} streaming={false} />);
    expect(screen.getByTestId('p').textContent).toBe(para);
  });
  it('text arriving while streaming is revealed over frames, then fully, even after the stream ends', () => {
    const { rerender } = render(<Probe text="" streaming />);
    rerender(<Probe text={para} streaming />);
    expect(screen.getByTestId('p').textContent!.length).toBeLessThan(para.length);
    rerender(<Probe text={para} streaming={false} />);
    expect(screen.getByTestId('p').dataset.pending).toBe('true');
    act(() => { vi.advanceTimersByTime(80); });
    const mid = screen.getByTestId('p').textContent!.length;
    expect(mid).toBeGreaterThan(0);
    expect(mid).toBeLessThan(para.length);
    act(() => { vi.advanceTimersByTime(1500); });
    expect(screen.getByTestId('p').textContent).toBe(para);
    expect(screen.getByTestId('p').dataset.pending).toBeUndefined();
  });
  it('reduced motion shows everything at once', () => {
    const mm = window.matchMedia;
    window.matchMedia = ((q: string) => ({ matches: q.includes('reduce'), media: q, addEventListener() {}, removeEventListener() {} })) as unknown as typeof window.matchMedia;
    try {
      const { rerender } = render(<Probe text="" streaming />);
      rerender(<Probe text={para} streaming />);
      expect(screen.getByTestId('p').textContent).toBe(para);
    } finally { window.matchMedia = mm; }
  });
  it('a shorter text (regenerate) shows immediately, no stale tail', () => {
    const { rerender } = render(<Probe text="" streaming />);
    rerender(<Probe text={para} streaming />);
    act(() => { vi.advanceTimersByTime(2000); });
    rerender(<Probe text="New." streaming />);
    expect(screen.getByTestId('p').textContent).toBe('New.');
  });
});

describe('usePacedText first delta', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => { vi.useRealTimers(); cleanup(); });
  it('a part that mounts streaming with its whole paragraph still flows in', () => {
    render(<Probe text={para} streaming />);
    expect(screen.getByTestId('p').textContent!.length).toBeLessThan(para.length);
    act(() => { vi.advanceTimersByTime(1500); });
    expect(screen.getByTestId('p').textContent).toBe(para);
  });
});
