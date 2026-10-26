import { describe, expect, it } from 'vitest';
import { rateLimitLabel, transportAnnouncement } from './useChat';

const seq = (labels: (string | null)[]) => {
  let prev: string | null = null;
  return labels.map((l) => { const a = transportAnnouncement(l, prev); prev = l; return a; });
};

describe('transportAnnouncement (aria-live 429 countdown)', () => {
  it('speaks the start of a wait, every 5 s, and the retry — not every tick', () => {
    const labels = [null, ...[12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1].map((s) => rateLimitLabel(s * 1000)), rateLimitLabel(0), 'Running query'];
    expect(seq(labels).filter(Boolean)).toEqual([
      'Busy right now. Retrying automatically in 12 seconds.',
      'Retrying in 10 seconds.',
      'Retrying in 5 seconds.',
      'Retrying now',
    ]);
  });
  it('a short Retry-After (3 s) is spoken once, singular seconds handled', () => {
    expect(seq([rateLimitLabel(3000), rateLimitLabel(2000), rateLimitLabel(1000)])).toEqual(['Busy right now. Retrying automatically in 3 seconds.', null, null]);
    expect(transportAnnouncement(rateLimitLabel(1000), null)).toBe('Busy right now. Retrying automatically in 1 second.');
  });
  it('a repeat 429 (count jumps back up) says "Still busy"', () => {
    expect(seq([rateLimitLabel(3000), rateLimitLabel(2000), rateLimitLabel(1000), rateLimitLabel(3000)]).at(-1)).toBe('Still busy. Retrying in 3 seconds.');
    expect(seq([rateLimitLabel(1000), rateLimitLabel(0), rateLimitLabel(3000)])).toEqual(['Busy right now. Retrying automatically in 1 second.', 'Retrying now', 'Still busy. Retrying in 3 seconds.']);
  });
  it('offline/reconnecting are spoken once; server statuses are left to the timeline', () => {
    expect(seq(['Offline, waiting for the network…', 'Offline, waiting for the network…', 'Reconnecting…', 'Running query'])).toEqual(['Offline, waiting for the network', null, 'Reconnecting', null]);
  });
});
