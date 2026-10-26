/**
 * Timezone and DST: dates shown and put in filenames follow the user's local calendar. Runs in America/Los_Angeles
 * (DST 2026: Mar 8 spring forward, Nov 1 fall back) by setting TZ, which Node applies at runtime.
 */
import { afterAll, beforeAll, describe, expect, it } from 'vitest';
import { localYmd } from './dates';
import { csvFilename } from './csv';
import { transcriptMarkdown } from '../shell/exportMarkdown';
import { bucket } from '../components/Sidebar';

const prevTz = process.env.TZ;
beforeAll(() => { process.env.TZ = 'America/Los_Angeles'; });
afterAll(() => { if (prevTz === undefined) delete process.env.TZ; else process.env.TZ = prevTz; });

describe('local calendar dates', () => {
  it('the TZ override is in effect', () => {
    expect(new Date('2026-10-26T05:00:00Z').getHours()).toBe(22); // 22:00 PDT
  });

  it('a 10 PM Pacific Markdown export says today, not the UTC tomorrow', () => {
    const late = new Date('2026-10-26T05:00:00Z'); // Oct 25, 22:00 PDT
    expect(localYmd(late)).toBe('2026-10-25');
    expect(transcriptMarkdown('Q', [], late)).toContain('on 2026-10-25.');
  });

  it('CSV names stay on the UTC day, matching the API (shared vectors)', () => {
    expect(csvFilename('Denials by payer', new Date('2026-10-26T05:00:00Z'))).toBe('cc_denials_by_payer_2026-10-26.csv');
  });
});

describe('sidebar buckets across DST', () => {
  it('the day after spring-forward: 23:30 two days ago is not "Yesterday"', () => {
    const now = new Date('2026-03-09T17:00:00Z'); // Mon Mar 9, 10:00 PDT; Sun Mar 8 was 23 h long
    expect(bucket('2026-03-08T07:30:00Z', now)).toBe('Previous 7 days'); // Sat Mar 7, 23:30 PST
    expect(bucket('2026-03-08T08:30:00Z', now)).toBe('Yesterday'); // Sun Mar 8, 00:30 PST
    expect(bucket('2026-03-09T07:30:00Z', now)).toBe('Today'); // Mon Mar 9, 00:30 PDT
  });

  it('the day after fall-back: 00:30 yesterday is "Yesterday" (that day had 25 h)', () => {
    const now = new Date('2026-11-02T18:00:00Z'); // Mon Nov 2, 10:00 PST
    expect(bucket('2026-11-01T07:30:00Z', now)).toBe('Yesterday'); // Sun Nov 1, 00:30 PDT
    expect(bucket('2026-11-01T06:30:00Z', now)).toBe('Previous 7 days'); // Sat Oct 31, 23:30 PDT
  });

  it('an unparseable timestamp lands in "Older", never "NaN"', () => {
    expect(bucket('not a date', new Date('2026-10-25T12:00:00Z'))).toBe('Older');
  });
});
