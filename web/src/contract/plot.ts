/**
 * Strict PlotSpec v1 (contract 1.18.0, CONTRACT.md §16.1–16.2): the zod mirror of `$defs.PlotSpec` in
 * api/contract/v1.schema.json. Enums, caps and patterns come from the generated `PLOT_SCHEMA`, so the two cannot
 * drift; api/contract/plot_cases.json is run by this file's tests and by api/tests/test_plot_118.py, and both must
 * report exactly the same rule ids.
 *
 *   checkPlot(plot)                 // → [] when valid, else sorted rule ids
 *   checkPlot(header, { header: true })  // a chart.start header (no data)
 *   parsePlot(plot)                 // → PlotSpec | null (validate before rendering)
 *   plotToVegaLite(plot)            // the same Vega-Lite spec the server derives (api/plot.py to_vega_lite)
 *
 * Two stages, like the server: structure (zod) first; the semantic pass (field references, series cap, temporal
 * values) only runs when the structure is valid. Pure and total: never throws. Uses `zod/mini` (tree-shakable) because
 * the reducer is in the initial bundle (web/bundle-budget.json).
 */
import * as z from 'zod/mini';
import type { $ZodIssue } from 'zod/v4/core';
import { PLOT_SCHEMA, type PlotHeader, type PlotSpec, type PlotRow } from './v1.gen';

export type PlotRule =
  | 'plot.type' | 'plot.required' | 'plot.closed' | 'plot.version' | 'plot.kind' | 'plot.enum'
  | 'plot.label.length' | 'plot.field.name' | 'plot.points.max' | 'plot.points.min' | 'plot.number.finite'
  | 'plot.field.duplicate' | 'plot.field.unknown' | 'plot.row.missing' | 'plot.series.max' | 'plot.temporal';

const C = PLOT_SCHEMA.caps;
const FIELD_RE = new RegExp(PLOT_SCHEMA.fieldPattern);
const TEMPORAL_RE = new RegExp(PLOT_SCHEMA.temporalPattern);
/** Lengths in code points, like JSON Schema `maxLength` (an emoji is 1, not 2 UTF-16 units). */
const cp = (s: string) => [...s].length;
const label = (max: number, min = 0) =>
  z.string().check(z.refine((s) => cp(s) >= min && cp(s) <= max, { params: { rule: 'plot.label.length' } }));
const field = z.string().check(z.regex(FIELD_RE));
const value = z.union([z.number(), label(C.maxString), z.null()]);

const axisX = z.strictObject({
  field, type: z.enum(PLOT_SCHEMA.fieldTypes), title: label(C.maxAxisTitle), format: z.optional(z.enum(PLOT_SCHEMA.formats)),
});
const axisY = z.strictObject({
  field, type: z.literal('quantitative'), title: label(C.maxAxisTitle), format: z.optional(z.enum(PLOT_SCHEMA.formats)),
});
const series = z.strictObject({ field, title: label(C.maxAxisTitle) });
const headerShape = {
  v: z.literal(PLOT_SCHEMA.version),
  kind: z.enum(PLOT_SCHEMA.kinds),
  title: label(C.maxTitle, 1),
  x: axisX,
  y: axisY,
  series: z.optional(series),
  sort: z.optional(z.enum(PLOT_SCHEMA.sorts)),
};
export const plotRowSchema = z.record(field, value);
export const plotHeaderSchema = z.strictObject(headerShape);
export const plotSpecSchema = z.strictObject({
  ...headerShape,
  data: z.array(plotRowSchema).check(z.minLength(C.minPoints), z.maxLength(C.maxPoints)),
});

type Issue = $ZodIssue;

function at(root: unknown, path: readonly PropertyKey[]): { parent: unknown; has: boolean; value: unknown } {
  let cur: unknown = root;
  let parent: unknown = undefined;
  let has = true;
  for (const k of path) {
    parent = cur;
    if (cur !== null && typeof cur === 'object' && Object.prototype.hasOwnProperty.call(cur, k as string)) {
      cur = (cur as Record<PropertyKey, unknown>)[k];
    } else {
      has = false;
      cur = undefined;
      break;
    }
  }
  return { parent, has, value: cur };
}

function rulesOf(issue: Issue, root: unknown, out: Set<PlotRule>): void {
  const path = issue.path;
  const last = path[path.length - 1];
  // A missing key fails as invalid_type (objects, strings) or invalid_value (literals, enums): both are `required`.
  if (path.length > 0 && issue.code !== 'unrecognized_keys' && !at(root, path).has) {
    out.add('plot.required');
    return;
  }
  switch (issue.code) {
    case 'unrecognized_keys': out.add('plot.closed'); return;
    case 'invalid_type': out.add('plot.type'); return;
    case 'invalid_value':
      out.add(path.length === 1 && last === 'v' ? 'plot.version' : path.length === 1 && last === 'kind' ? 'plot.kind' : 'plot.enum');
      return;
    case 'invalid_format': out.add('plot.field.name'); return;
    case 'invalid_key': out.add('plot.field.name'); return;
    case 'too_big': out.add(issue.origin === 'array' ? 'plot.points.max' : 'plot.label.length'); return;
    case 'too_small': out.add(issue.origin === 'array' ? 'plot.points.min' : 'plot.label.length'); return;
    case 'custom': out.add(((issue.params as { rule?: PlotRule } | undefined)?.rule) ?? 'plot.type'); return;
    case 'invalid_union': {
      const { value: v } = at(root, path);
      out.add(typeof v === 'string' ? 'plot.label.length' : typeof v === 'number' ? 'plot.number.finite' : 'plot.type');
      return;
    }
    default: out.add('plot.type');
  }
}

const isNum = (v: unknown): v is number => typeof v === 'number';

/** zod's record skips own `__proto__` keys (JSON.parse creates them); check them like any other row key. */
function skippedKeys(plot: unknown, out: Set<PlotRule>): void {
  const data = plot !== null && typeof plot === 'object' ? (plot as { data?: unknown }).data : undefined;
  if (!Array.isArray(data)) return;
  for (const row of data) {
    if (row === null || typeof row !== 'object' || Array.isArray(row) || !Object.prototype.hasOwnProperty.call(row, '__proto__')) continue;
    const v = Object.getOwnPropertyDescriptor(row, '__proto__')?.value;
    const ok = value.safeParse(v);
    if (!ok.success) out.add(typeof v === 'string' ? 'plot.label.length' : typeof v === 'number' ? 'plot.number.finite' : 'plot.type');
  }
}

function semantic(plot: PlotHeader & { data?: PlotRow[] }): Set<PlotRule> {
  const out = new Set<PlotRule>();
  const { x, y, series: s } = plot;
  const fields = [x.field, y.field, ...(s ? [s.field] : [])];
  if (new Set(fields).size !== fields.length) out.add('plot.field.duplicate');
  const distinct = new Set<unknown>();
  for (const row of plot.data ?? []) {
    if (Object.keys(row).some((k) => !fields.includes(k))) out.add('plot.field.unknown');
    const xv = Object.prototype.hasOwnProperty.call(row, x.field) ? row[x.field] : null;
    if (xv === null || !Object.prototype.hasOwnProperty.call(row, y.field)) out.add('plot.row.missing');
    else if (x.type === 'quantitative' && !isNum(xv)) out.add('plot.type');
    else if (x.type === 'temporal' && !(typeof xv === 'string' && TEMPORAL_RE.test(xv))) out.add('plot.temporal');
    const yv = Object.prototype.hasOwnProperty.call(row, y.field) ? row[y.field] : null;
    if (yv !== null && !isNum(yv)) out.add('plot.type');
    if (s) {
      const sv = Object.prototype.hasOwnProperty.call(row, s.field) ? row[s.field] : null;
      if (sv === null) out.add('plot.row.missing');
      else distinct.add(sv);
    }
  }
  if (distinct.size > C.maxSeries) out.add('plot.series.max');
  return out;
}

/** Rule ids `plot` violates, sorted and unique; `[]` means valid. Never throws. */
export function checkPlot(plot: unknown, opts: { header?: boolean } = {}): PlotRule[] {
  const schema = opts.header ? plotHeaderSchema : plotSpecSchema;
  let res;
  try {
    res = schema.safeParse(plot);
  } catch {
    return ['plot.type'];
  }
  const out = new Set<PlotRule>();
  if (!res.success) res.error.issues.forEach((i) => rulesOf(i, plot, out));
  skippedKeys(plot, out);
  // Semantics read the input, not zod's output: zod drops a `__proto__` key that the server sees as a field.
  if (!out.size) semantic(plot as PlotHeader & { data?: PlotRow[] }).forEach((r) => out.add(r));
  return [...out].sort();
}

/** The plot if it is valid, else `null`. Clients validate before rendering and drop an invalid chart. */
export function parsePlot(plot: unknown): PlotSpec | null {
  return checkPlot(plot).length ? null : (plot as PlotSpec);
}

export const D3_FORMATS: Record<PlotSpec['y']['format'] & string, string> = {
  currency: '$,.2f', percent: '.1%', integer: ',d', number: ',.2~f', date: '%b %-d, %Y', month: '%b %Y',
};

/** The Vega-Lite spec for a valid plot (mirrors api/plot.py `to_vega_lite`; data is named `result`). */
export function plotToVegaLite(plot: PlotHeader): Record<string, unknown> {
  const { x, y, series: s } = plot;
  const fields = [x.field, y.field, ...(s ? [s.field] : [])];
  const xenc: Record<string, unknown> = { field: x.field, type: x.type, title: x.title };
  if (x.format) xenc.axis = { format: D3_FORMATS[x.format] };
  const yenc: Record<string, unknown> = { field: y.field, type: 'quantitative', title: y.title };
  if (y.format) yenc.axis = { format: D3_FORMATS[y.format] };
  const tooltip = fields.map((f) => ({
    field: f,
    type: f === y.field ? 'quantitative'
      : f === x.field && x.type === 'temporal' ? 'temporal'
        : f === x.field && x.type === 'quantitative' ? 'quantitative' : 'nominal',
  }));
  const enc: Record<string, unknown> = { x: xenc, y: yenc, tooltip };
  const sort = plot.sort ?? (plot.kind === 'bar' ? '-y' : 'none');
  if (sort === '-y') xenc.sort = '-y';
  else if (sort === 'x') xenc.sort = 'ascending';
  if (s) enc.color = { field: s.field, type: 'nominal', title: s.title };
  return {
    $schema: 'https://vega.github.io/schema/vega-lite/v5.json', data: { name: 'result' },
    mark: { type: plot.kind, point: plot.kind === 'line', tooltip: true }, encoding: enc, width: 'container', height: 280,
  };
}

/** Header fields of a full plot (what `chart.start` carries). */
export const headerOf = (plot: PlotSpec): PlotHeader => {
  const { data: _data, ...header } = plot;
  return header;
};
