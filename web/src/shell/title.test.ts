import { describe, expect, it } from 'vitest';
import { shortTitle } from './title';

describe('shortTitle', () => {
  it.each([
    ['What was our denial rate by payer last quarter?', 'Denial rate by payer'],
    ['How many claims were denied in Q3 2026?', 'Claims were denied'],
    ['Show paid amount by month for 2025-10..2026-09', 'Paid amount by month'],
    ['Denials last week', 'Denials last week'],
    ['Show the monthly trend of paid amount', 'Monthly trend of paid amount'],
    ['Can you show me the top 10 CARC codes for Aetna in Q3?', 'Top 10 CARC codes for Aetna'],
    ['Which payers have the slowest time to payment?', 'Payers have the slowest time'],
    ['hi', 'Hi'],
    ['', 'New chat'],
  ])('%s -> %s', (q, want) => {
    expect(shortTitle(q)).toBe(want);
  });
  it('stays short', () => {
    const t = shortTitle('Please break down the denial reasons for every single payer we have across all of the regions over time');
    expect(t.length).toBeLessThanOrEqual(36);
    expect(t.split(' ').length).toBeLessThanOrEqual(6);
  });
});
