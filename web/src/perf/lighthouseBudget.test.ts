import { describe, expect, it } from 'vitest';
import { evaluate, formatRows, loadByteWeight, median } from '../../scripts/lighthouse_budget.mjs';
import config from '../../scripts/perf_budget.json';

const budget = { categories: { performance: 0.9, accessibility: 1 }, metrics: { 'largest-contentful-paint': 2500, 'cumulative-layout-shift': 0.02 } };
const report = (perf: number, lcp: number, cls = 0) => ({
  categories: { performance: { score: perf }, accessibility: { score: 1 } },
  audits: { 'largest-contentful-paint': { numericValue: lcp }, 'cumulative-layout-shift': { numericValue: cls } },
});

describe('lighthouse budget', () => {
  it('medians', () => {
    expect(median([3, 1, 2])).toBe(2);
    expect(median([4, 1, 3, 2])).toBe(2.5);
  });
  it('passes within budget and uses the median run (one noisy run does not fail CI)', () => {
    const { rows, errors } = evaluate('mobile', [report(0.95, 2000), report(0.6, 9000), report(0.93, 2100)], budget);
    expect(errors).toEqual([]);
    expect(rows.every((r) => r.ok)).toBe(true);
    expect(rows.find((r) => r.key === 'largest-contentful-paint')?.value).toBe(2100);
  });
  it('fails a regression: categories are minimums, metrics maximums', () => {
    const { rows } = evaluate('mobile', [report(0.8, 3000, 0.1)], budget);
    expect(rows.filter((r) => !r.ok).map((r) => r.key)).toEqual(['performance', 'largest-contentful-paint', 'cumulative-layout-shift']);
    expect(formatRows(rows)).toMatch(/FAIL mobile  performance\s+0\.80 >= 0\.90/);
  });
  it('fails on a missing metric or a Lighthouse runtime error, never passes vacuously', () => {
    const missing = { categories: { performance: { score: 1 }, accessibility: { score: 1 } }, audits: {} };
    expect(evaluate('desktop', [missing], budget).rows.some((r) => !r.ok)).toBe(true);
    const crashed = { ...report(1, 100), runtimeError: { code: 'NO_FCP' } };
    const res = evaluate('desktop', [crashed], budget);
    expect(res.errors.join()).toMatch(/NO_FCP/);
    expect(res.rows).toEqual([]);
  });
  it('committed budget keeps the bar: a11y 1.0 on both form factors, pinned Lighthouse', () => {
    expect(config.lighthouse).toMatch(/^\d+\.\d+\.\d+$/);
    for (const p of Object.values(config.presets)) {
      expect(p.categories.accessibility).toBe(1);
      expect(p.metrics['cumulative-layout-shift']).toBeLessThanOrEqual(0.1);
      expect(p.metrics['load-byte-weight']).toBeLessThanOrEqual(400 * 1024); // the cold load
      expect(p.metrics['total-byte-weight']).toBeLessThanOrEqual(800 * 1024); // plus the idle prefetch
    }
  });

  describe('load-byte-weight (the cold load, without the idle prefetch)', () => {
    const net = (load: number, reqs: [number, number][]) => ({
      categories: {},
      audits: {
        metrics: { details: { items: [{ observedLoad: load }] } },
        'network-requests': { details: { items: reqs.map(([t, size]) => ({ networkRequestTime: t, transferSize: size })) } },
        'total-byte-weight': { numericValue: reqs.reduce((n, [, b]) => n + b, 0) },
      },
    });
    // Shape of the 00:40 run: html + entry js + runtime + css by load (74 ms), then the prefetched chunks.
    const real = net(74, [[1, 10116], [21, 164482], [22, 2124], [22, 18210], [171, 147858], [227, 82482], [256, 107406], [256, 184164]]);

    it('counts only requests started by the load event', () => {
      expect(loadByteWeight(real)).toBe(10116 + 164482 + 2124 + 18210);
      expect(loadByteWeight(net(100, [[100, 5], [101, 7]]))).toBe(5); // at load counts, after load doesn't
    });
    it('is budgeted separately from the prefetch-inclusive total', () => {
      const b = { categories: {}, metrics: { 'load-byte-weight': 358400, 'total-byte-weight': 819200 } };
      expect(evaluate('desktop', [real], b).rows.map((r) => [r.key, r.ok])).toEqual([['load-byte-weight', true], ['total-byte-weight', true]]);
      // a cold-load regression (a big chunk pulled into the entry) still fails even though the total is fine
      const heavy = net(74, [[1, 10116], [21, 400000], [171, 100000]]);
      expect(evaluate('desktop', [heavy], b).rows.find((r) => r.key === 'load-byte-weight')?.ok).toBe(false);
    });
    it('fails closed when the report lacks the network or metrics audits', () => {
      const bare = { categories: {}, audits: { 'total-byte-weight': { numericValue: 1 } } };
      expect(loadByteWeight(bare)).toBeNull();
      const row = evaluate('mobile', [bare], { categories: {}, metrics: { 'load-byte-weight': 358400 } }).rows[0];
      expect(row.ok).toBe(false);
    });
  });
});
