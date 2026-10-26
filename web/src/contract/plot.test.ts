import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { checkPlot, headerOf, parsePlot, plotToVegaLite, plotSpecSchema } from './plot';
import { PLOT_SCHEMA, type PlotSpec } from './v1.gen';
import { reduceV1, newAssistantViewV1, type AssistantViewV1, type StreamEvent } from '../api/contract';
import { buildSpec, chartRenderer } from '../render/chartSpec';

const API = resolve(__dirname, '../../../api/contract');

/** The shared cases' sentinels (api/contract/make_plot_cases.py `expand`). */
function expand(v: unknown): unknown {
  if (Array.isArray(v)) return v.map(expand);
  if (v !== null && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    const keys = Object.keys(o);
    if (keys.length === 1 && keys[0] === '$nonfinite') return { NaN: NaN, Infinity: Infinity, '-Infinity': -Infinity }[o.$nonfinite as string];
    if (keys.length === 1 && keys[0] === '$repeat') { const [t, n] = o.$repeat as [string, number]; return t.repeat(n); }
    if (keys.length === 1 && keys[0] === '$range') {
      const [n, xf, yf] = o.$range as [number, string, string];
      return Array.from({ length: n }, (_, i) => ({ [xf]: `P${i}`, [yf]: i }));
    }
    return Object.fromEntries(Object.entries(o).map(([k, x]) => [k, expand(x)]));
  }
  return v;
}

type Case = { name: string; header?: boolean; plot: unknown; rules: string[] };
const file = JSON.parse(readFileSync(resolve(API, 'plot_cases.json'), 'utf8')) as { cases: Case[] };
const cases = file.cases.map((c) => ({ ...c, plot: expand(c.plot) }));

describe('PlotSpec v1 zod mirror runs the shared cases (api/contract/plot_cases.json)', () => {
  it('has cases for every rule id', () => {
    const seen = new Set(cases.flatMap((c) => c.rules));
    for (const r of ['plot.type', 'plot.required', 'plot.closed', 'plot.version', 'plot.kind', 'plot.enum', 'plot.label.length',
      'plot.field.name', 'plot.points.max', 'plot.points.min', 'plot.number.finite', 'plot.field.duplicate', 'plot.field.unknown',
      'plot.row.missing', 'plot.series.max', 'plot.temporal']) expect(seen, r).toContain(r);
  });
  it.each(cases.map((c) => [c.name, c] as const))('%s', (_n, c) => {
    expect(checkPlot(c.plot, { header: c.header })).toEqual(c.rules);
  });
});

describe('PlotSpec caps come from the schema', () => {
  it('uses the generated caps and enums', () => {
    expect(PLOT_SCHEMA.caps.maxPoints).toBe(2000);
    expect(PLOT_SCHEMA.caps.maxSeries).toBe(10);
    expect(PLOT_SCHEMA.kinds).toEqual(['bar', 'line', 'area', 'point']);
    expect(plotSpecSchema.shape.kind.options).toEqual([...PLOT_SCHEMA.kinds]);
  });
  it('never throws on hostile input', () => {
    const cyclic: Record<string, unknown> = { v: 1 };
    cyclic.self = cyclic;
    const getter = Object.defineProperty({}, 'v', { get() { throw new Error('boom'); }, enumerable: true });
    for (const x of [undefined, 0, 'x', [], cyclic, getter, Object.create(null), new Proxy({}, {})]) {
      expect(() => checkPlot(x)).not.toThrow();
      expect(checkPlot(x).length).toBeGreaterThan(0);
    }
  });
  it('rejects a __proto__ row key as an unknown field, never pollutes', () => {
    const plot = JSON.parse('{"v":1,"kind":"bar","title":"t","x":{"field":"a","type":"nominal","title":""},"y":{"field":"b","type":"quantitative","title":""},"data":[{"a":"x","b":1,"__proto__":{"polluted":1}}]}');
    expect(checkPlot(plot)).toEqual(['plot.type']);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });
});

const exampleCharts = (): PlotSpec[] => {
  const out: PlotSpec[] = [];
  for (const dir of [API + '/examples', API + '/examples/edge']) {
    for (const f of readdirSync(dir).filter((n) => n.endsWith('.jsonl'))) {
      for (const line of readFileSync(resolve(dir, f), 'utf8').split('\n').filter(Boolean)) {
        const ev = JSON.parse(line) as { type: string; part?: { type: string; plot?: PlotSpec; spec?: unknown; content?: { type: string; plot?: PlotSpec; spec?: unknown } } };
        const p = ev.type === 'part' ? (ev.part?.type === 'artifact' ? ev.part.content : ev.part) : undefined;
        if (p?.type === 'chart') {
          expect(p.plot, `${f}: a 1.18 chart carries plot`).toBeDefined();
          expect(checkPlot(p.plot)).toEqual([]);
          // the server's derived spec equals the client's derivation (api/plot.py to_vega_lite ≡ plotToVegaLite)
          expect(plotToVegaLite(p.plot!)).toEqual(p.spec);
          out.push(p.plot!);
        }
      }
    }
  }
  return out;
};

describe('Vega-Lite derivation', () => {
  it('matches the server for every example chart', () => {
    expect(exampleCharts().length).toBeGreaterThan(5);
  });
  it('buildSpec renders from a valid plot (data from plot.data) and falls back to spec without one', () => {
    const plot = exampleCharts()[0];
    const part = { type: 'chart' as const, id: 'c', library: 'vega-lite' as const, spec: { mark: 'point' }, alt: 'a', plot };
    const spec = buildSpec(part);
    expect((spec.data as { values: unknown[] }).values).toEqual(plot.data);
    expect((spec.mark as { type: string }).type).toBe(plot.kind);
    expect(buildSpec({ ...part, plot: undefined }).mark).toBe('point');
    expect(chartRenderer(part)).toBe('svg');
  });
  it('maps formats, sorts and quantitative x', () => {
    const base = { v: 1 as const, kind: 'point' as const, title: 't', x: { field: 'n', type: 'quantitative' as const, title: 'N', format: 'integer' as const }, y: { field: 'v', type: 'quantitative' as const, title: 'V', format: 'number' as const }, sort: 'x' as const };
    const s = plotToVegaLite(base) as { encoding: { x: Record<string, unknown>; y: Record<string, unknown>; tooltip: { type: string }[] } };
    expect(s.encoding.x).toMatchObject({ axis: { format: ',d' }, sort: 'ascending' });
    expect(s.encoding.y).toMatchObject({ axis: { format: ',.2~f' } });
    expect(s.encoding.tooltip.map((t) => t.type)).toEqual(['quantitative', 'quantitative']);
  });
});

// ------------------------------------------------------------- progressive rendering (§16.2) in the reducer
const HDR = { v: 1, kind: 'line', title: 'Denial rate by month', x: { field: 'month', type: 'temporal', title: 'Month' }, y: { field: 'rate', type: 'quantitative', title: 'Rate', format: 'percent' } } as const;
let n = 0;
const s = (e: Record<string, unknown>) => ({ ...e, seq: n++ }) as unknown as StreamEvent;
const run = (evs: StreamEvent[], m: AssistantViewV1 = newAssistantViewV1('m1')) => evs.reduce<AssistantViewV1>((v, e) => reduceV1(v, e), m);
const start = () => { n = 0; return s({ type: 'message.start', messageId: 'm1', conversationId: 'c1', contractVersion: '1.18.0' }); };
const pts = (from: number, k: number) => Array.from({ length: k }, (_, i) => ({ month: `2026-${String(from + i + 1).padStart(2, '0')}`, rate: (from + i) / 100 }));

describe('chart.start / chart.points (progressive rendering)', () => {
  it('draws from the header, grows with points, and is replaced by the canonical part', () => {
    let v = run([start(), s({ type: 'chart.start', partId: 'c', plot: HDR, alt: 'Line chart' })]);
    const first = v.parts[0] as { plot: PlotSpec };
    expect(first.plot.data).toEqual([]);
    expect(checkPlot(headerOf(first.plot), { header: true })).toEqual([]);
    v = run([s({ type: 'chart.points', partId: 'c', offset: 0, rows: pts(0, 3) })], v);
    expect((v.parts[0] as { plot: PlotSpec }).plot.data).toHaveLength(3);
    expect(parsePlot((v.parts[0] as { plot: PlotSpec }).plot)).not.toBeNull();
    v = run([s({ type: 'chart.points', partId: 'c', offset: 3, rows: pts(3, 2) })], v);
    const full = { ...HDR, data: pts(0, 5) };
    v = run([s({ type: 'part', part: { type: 'chart', id: 'c', library: 'vega-lite', spec: plotToVegaLite(full), data: { name: 'result', values: full.data }, alt: 'a', plot: full } })], v);
    expect(v.parts).toHaveLength(1);
    expect(v.streamingCharts).toEqual([]);
  });
  it('a replayed chunk overwrites in place; a chunk past the end (gap) is ignored', () => {
    let v = run([start(), s({ type: 'chart.start', partId: 'c', plot: HDR }), s({ type: 'chart.points', partId: 'c', offset: 0, rows: pts(0, 4) })]);
    v = run([s({ type: 'chart.points', partId: 'c', offset: 0, rows: pts(0, 1) })], v);
    expect((v.parts[0] as { plot: PlotSpec }).plot.data).toHaveLength(4);
    v = run([s({ type: 'chart.points', partId: 'c', offset: 9, rows: pts(9, 1) })], v);
    expect((v.parts[0] as { plot: PlotSpec }).plot.data).toHaveLength(4);
  });
  it('invalid points drop the chart (rule ids recorded, no values)', () => {
    const v = run([start(), s({ type: 'chart.start', partId: 'c', plot: HDR }), s({ type: 'chart.points', partId: 'c', offset: 0, rows: [{ month: 'January', rate: 1 }] })]);
    expect(v.parts).toEqual([]);
    expect(v.rejectedCharts).toEqual([{ partId: 'c', rules: ['plot.temporal'] }]);
    expect(JSON.stringify(v.rejectedCharts)).not.toContain('January');
  });
  it('points past the cap drop the chart', () => {
    let v = run([start(), s({ type: 'chart.start', partId: 'c', plot: { ...HDR, kind: 'point', x: { field: 'month', type: 'nominal', title: 'M' } } })]);
    for (let off = 0; off <= 2000; off += 500) v = run([s({ type: 'chart.points', partId: 'c', offset: off, rows: Array.from({ length: 500 }, (_, i) => ({ month: `m${off + i}`, rate: 1 })) })], v);
    expect(v.parts).toEqual([]);
    expect(v.rejectedCharts?.[0].rules).toEqual(['plot.points.max']);
  });
  it('an invalid final part (or chart artifact) is dropped; a legacy chart without plot still renders', () => {
    const bad = { ...HDR, kind: 'pie', data: pts(0, 2) };
    let v = run([start(), s({ type: 'part', part: { type: 'chart', id: 'c', library: 'vega-lite', spec: {}, alt: 'a', plot: bad } })]);
    expect(v.parts).toEqual([]);
    v = run([s({ type: 'part', part: { type: 'artifact', id: 'a1', artifactId: 'af', version: 1, kind: 'chart', title: 't', content: { type: 'chart', id: 'c2', library: 'vega-lite', spec: {}, alt: 'a', plot: bad } } })], v);
    expect(v.parts).toEqual([]);
    expect(v.rejectedCharts?.map((r) => r.partId)).toEqual(['c', 'a1']);
    v = run([s({ type: 'part', part: { type: 'chart', id: 'c3', library: 'vega-lite', spec: { mark: 'bar' }, alt: 'a' } })], v);
    expect(v.parts.map((p) => p.id)).toEqual(['c3']);
  });
  it('message.end drops a chart whose part never came', () => {
    const v = run([start(), s({ type: 'chart.start', partId: 'c', plot: HDR }), s({ type: 'chart.points', partId: 'c', offset: 0, rows: pts(0, 2) }),
      s({ type: 'message.end', messageId: 'm1', finishReason: 'error', usage: { queries: 0, elapsedMs: 1 } })]);
    expect(v.parts).toEqual([]);
    expect(v.streamingCharts).toEqual([]);
  });
  it('malformed chart.points (non-object rows) is dropped by wellFormed', () => {
    const v = run([start(), s({ type: 'chart.start', partId: 'c', plot: HDR })]);
    expect(run([s({ type: 'chart.points', partId: 'c', offset: 0, rows: [['2026-01', 1]] })], v)).toEqual({ ...v, lastSeq: v.lastSeq });
  });
});

describe('table.rows replay (edge case found while adding chart.points)', () => {
  it('an early replayed chunk no longer truncates rows after it', () => {
    const cols = [{ key: 'a', label: 'A', type: 'string' }];
    let v = run([start(), s({ type: 'table.start', partId: 't', columns: cols }), s({ type: 'table.rows', partId: 't', offset: 0, rows: [['1'], ['2']] }),
      s({ type: 'table.rows', partId: 't', offset: 2, rows: [['3'], ['4']] })]);
    v = run([s({ type: 'table.rows', partId: 't', offset: 0, rows: [['1'], ['2']] })], v);
    expect((v.parts[0] as { rows: unknown[] }).rows).toEqual([['1'], ['2'], ['3'], ['4']]);
    v = run([s({ type: 'table.rows', partId: 't', offset: 7, rows: [['8']] })], v);
    expect((v.parts[0] as { rows: unknown[] }).rows).toHaveLength(4);
  });
});
