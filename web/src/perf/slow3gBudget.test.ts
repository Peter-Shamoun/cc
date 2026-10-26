import { describe, expect, it } from 'vitest';
import { BUDGET, evaluate } from '../../scripts/slow3g_budget.mjs';

const within = { desktop: { fcp: 2100, composer: 2200 }, mobile: { fcp: 2200, composer: 2300 } };

describe('slow-3G first-paint budget', () => {
  it('passes a report within budget', () => {
    expect(evaluate(within)).toEqual([]);
  });

  it('fails a slow first paint with the form factor and numbers', () => {
    expect(evaluate({ ...within, mobile: { fcp: 10100, composer: 12000 } })).toEqual([
      `mobile fcp: 10100 ms > ${BUDGET.mobile.fcp} ms`,
      `mobile composer: 12000 ms > ${BUDGET.mobile.composer} ms`,
    ]);
  });

  it('fails a metric that was not measured (no silent pass)', () => {
    expect(evaluate({ desktop: { fcp: null, composer: 100 } })).toEqual([
      'desktop fcp: not measured',
      'mobile fcp: not measured',
      'mobile composer: not measured',
    ]);
  });

  it('keeps first paint under 2.5 s, the Lighthouse mobile FCP budget', () => {
    expect(BUDGET.desktop.fcp).toBeLessThanOrEqual(2500);
    expect(BUDGET.mobile.fcp).toBeLessThanOrEqual(2500);
  });
});
