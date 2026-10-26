import { describe, expect, it } from 'vitest';
import { buildSpec, chartRenderer, readableBars } from './chartSpec';
import type { ChartPart } from '../lib/contract';

const bars = (n: number, label = (i: number) => `P${i}`): Record<string, unknown> => ({
  mark: 'bar',
  encoding: { x: { field: 'payer', type: 'nominal' }, y: { field: 'paid', type: 'quantitative' } },
  data: { values: Array.from({ length: n }, (_, i) => ({ payer: label(i), paid: i })) },
});

describe('chart edge cases', () => {
  it('one category keeps a slim bar with a level label', () => {
    const out = readableBars(bars(1)) as { mark: { width: { band: number } }; encoding: { x: { axis: { labelAngle: number } } } };
    expect(out.mark.width.band).toBe(0.5);
    expect(out.encoding.x.axis.labelAngle).toBe(0);
  });
  it('500 categories flip to horizontal, cap the height and hide colliding labels', () => {
    const out = readableBars(bars(500)) as { height: number; encoding: { y: { field: string; axis: { labelOverlap: string } } } };
    expect(out.encoding.y.field).toBe('payer');
    expect(out.height).toBe(640);
    expect(out.encoding.y.axis.labelOverlap).toBe('greedy');
  });
  it('20 categories keep every label (no overlap rule)', () => {
    const out = readableBars(bars(20)) as { encoding: { y: { axis: Record<string, unknown> } } };
    expect(out.encoding.y.axis.labelOverlap).toBeUndefined();
  });
  it('empty, missing or malformed data never throws', () => {
    expect(() => buildSpec({ type: 'chart', id: 'c', library: 'vega-lite', spec: null, alt: 'x' } as unknown as ChartPart)).not.toThrow();
    expect(buildSpec({ type: 'chart', id: 'c', library: 'vega-lite', spec: { mark: 'bar' }, data: { name: 'r', values: 'oops' }, alt: 'x' } as unknown as ChartPart).data).toEqual({ values: [] });
    expect(readableBars(bars(0))).toBeTruthy();
    expect(chartRenderer({ type: 'chart', id: 'c', library: 'vega-lite', spec: {}, alt: 'x', data: { name: 'r', values: Array.from({ length: 5000 }, () => ({})) } } as ChartPart)).toBe('canvas');
  });
});
