import type { ChartPart } from '../lib/contract';
import { parsePlot, plotToVegaLite } from '../contract/plot';

const DANGEROUS_PROTOCOL = /^(javascript|data|vbscript|file|blob):/i;

function isRecord(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

/** Drop remote fetches, embed-option injection, and active-content URLs from a Vega spec. */
function sanitize(value: unknown): unknown {
  if (typeof value === 'string') return DANGEROUS_PROTOCOL.test(value.trim()) ? undefined : value;
  if (Array.isArray(value)) return value.map(sanitize).filter((v) => v !== undefined);
  if (!isRecord(value)) return value;
  const out: Record<string, unknown> = {};
  for (const [key, child] of Object.entries(value)) {
    if (key === '__proto__' || key === 'constructor' || key === 'prototype') continue;
    // $schema trips Vega-Lite 6 warnings; usermeta.embedOptions can inject a loader or style.
    if (key === '$schema' || key === 'usermeta' || key === 'url' || key === 'href') continue;
    if (key === 'data' && isRecord(child) && typeof child.url === 'string') continue;
    const next = sanitize(child);
    if (next !== undefined) out[key] = next;
  }
  return out;
}

/** Inline `part.data.values` only. Spec `data.url` (including nested layers) is removed. */
export function buildSpec(part: ChartPart): Record<string, unknown> {
  // 1.18.0: a valid strict `plot` is the source; the spec is derived from it (contract §16.1). Pre-1.18 parts use `spec`.
  const plot = part?.plot !== undefined ? parsePlot(part.plot) : null;
  if (plot) {
    const derived = sanitize(plotToVegaLite(plot)) as Record<string, unknown>;
    delete derived.data;
    return { ...derived, data: { values: plot.data } };
  }
  const cleaned = (isRecord(part?.spec) ? sanitize(part.spec) : {}) as Record<string, unknown>;
  delete cleaned.data;
  const values = Array.isArray(part?.data?.values) ? part.data.values : [];
  return { ...cleaned, data: { values } };
}

/** Spec §4.5: SVG under 2k marks, canvas above that so a huge series cannot build a giant DOM. */
export function chartRenderer(part: ChartPart): 'svg' | 'canvas' {
  const n = part?.plot?.data?.length ?? part?.data?.values?.length ?? 0;
  return n > 2000 ? 'canvas' : 'svg';
}

type Enc = Record<string, unknown> & { field?: string; type?: string; sort?: unknown; axis?: Record<string, unknown> | null };

const LONG_LABEL = 8;
const MANY_BARS = 5;

/**
 * QA-A12: vertical bars with many or long category labels force Vega to rotate them 90°
 * and clip them. ChatGPT/Claude flip those to horizontal bars so every label reads left to
 * right. Short, few labels stay vertical with level (0°) labels. Only plain single-view bar
 * specs are touched; layered, faceted, or already-horizontal specs pass through unchanged.
 */
export function readableBars(spec: Record<string, unknown>): Record<string, unknown> {
  const mark = spec.mark;
  const markType = typeof mark === 'string' ? mark : isRecord(mark) ? mark.type : undefined;
  const enc = spec.encoding;
  if (markType !== 'bar' || !isRecord(enc) || spec.layer || spec.facet || spec.repeat) return spec;
  const x = enc.x as Enc | undefined, y = enc.y as Enc | undefined;
  if (!isRecord(x) || !isRecord(y) || !x.field || y.type !== 'quantitative') return spec;
  if (x.type !== 'nominal' && x.type !== 'ordinal') return spec;
  if (enc.xOffset || enc.column || enc.row) return spec;
  const values = (isRecord(spec.data) && Array.isArray(spec.data.values) ? spec.data.values : []) as unknown[];
  const labels = new Set(values.map((r) => (isRecord(r) ? String(r[x.field as string] ?? '') : '')));
  const longest = Math.max(0, ...[...labels].map((l) => l.length));
  if (labels.size <= MANY_BARS && longest <= LONG_LABEL) {
    const axis = x.axis === null ? null : { labelAngle: 0, labelLimit: 120, ...x.axis };
    // One or two categories would stretch into slabs across the whole width: keep bars half a band wide.
    const slim = labels.size <= 2 ? { mark: { ...(isRecord(mark) ? mark : { type: 'bar' }), width: { band: 0.5 } } } : {};
    return { ...spec, ...slim, encoding: { ...enc, x: { ...x, axis } } };
  }
  const sort = x.sort === '-y' ? '-x' : x.sort === 'y' ? 'x' : x.sort;
  const flipped: Record<string, unknown> = {
    ...enc,
    x: y.axis === null ? y : { ...y, axis: { tickCount: 6, ...y.axis } },
    // The category axis title would sit rotated behind long labels; the chart title already names it.
    // Hundreds of categories cannot all get a readable row in 640px: hide colliding labels instead of overprinting them.
    y: { ...x, title: null, ...(sort !== undefined ? { sort } : {}), axis: x.axis === null ? null : { labelLimit: 200, ...(labels.size * 28 > 640 ? { labelOverlap: 'greedy', labelFontSize: 10 } : {}), ...x.axis, labelAngle: 0 } },
  };
  const height = Math.min(Math.max(labels.size * 28, 140), 640);
  return { ...spec, encoding: flipped, height };
}

/**
 * ChatGPT/Claude charts always answer a hover. Single-view specs that set no tooltip get
 * `mark.tooltip: true` (Vega-Lite then shows every encoded field). Explicit tooltips, including
 * `tooltip: false` / `null`, and layered or composed specs are left alone.
 */
export function withTooltips(spec: Record<string, unknown>): Record<string, unknown> {
  if (spec.layer || spec.facet || spec.repeat || spec.concat || spec.hconcat || spec.vconcat) return spec;
  const mark = spec.mark;
  if (mark === undefined) return spec;
  if (isRecord(spec.encoding) && 'tooltip' in spec.encoding) return titledTooltips(spec);
  if (isRecord(mark)) return 'tooltip' in mark ? spec : { ...spec, mark: { ...mark, tooltip: true } };
  return typeof mark === 'string' ? { ...spec, mark: { type: mark, tooltip: true } } : spec;
}

/**
 * Tooltip rows read like the axes ("CARC code: CO-97", "Denial rate: 20.6%"), not raw column keys
 * ("carc_code"). Untitled tooltip fields borrow the title and number format of the channel that
 * plots the same field. Call before readableBars, which blanks the category axis title.
 */
export function titledTooltips(spec: Record<string, unknown>): Record<string, unknown> {
  const enc = spec.encoding;
  if (!isRecord(enc) || !Array.isArray(enc.tooltip)) return spec;
  const channels = Object.entries(enc).filter(([k, v]) => k !== 'tooltip' && isRecord(v) && typeof v.field === 'string') as [string, Enc][];
  let changed = false;
  const tooltip = enc.tooltip.map((t: unknown) => {
    if (!isRecord(t) || typeof t.field !== 'string') return t;
    const ch = channels.find(([, v]) => v.field === t.field)?.[1];
    if (!ch) return t;
    const title = t.title ?? ch.title ?? ch.axis?.title ?? (isRecord(ch.legend) ? ch.legend.title : undefined);
    const format = t.format ?? ch.format ?? ch.axis?.format;
    const next = { ...t, ...(title != null && t.title === undefined ? { title } : {}), ...(format != null && t.format === undefined ? { format } : {}) };
    if (Object.keys(next).length !== Object.keys(t).length) changed = true;
    return next;
  });
  return changed ? { ...spec, encoding: { ...enc, tooltip } } : spec;
}

/**
 * Dates in answers are calendar dates ("2025-10-01" = October), which Vega parses as UTC midnight.
 * Shown in local time they slip a day (Sep 30) and a monthly axis/tooltip starts a month early.
 * Read every temporal field in UTC: `timeUnit` gets its `utc` prefix, positional channels without a
 * timeUnit get a utc scale, and tooltip entries without one get `utcyearmonthdate`.
 */
const POSITIONAL = new Set(['x', 'y', 'x2', 'y2']);
function utcUnit(u: unknown): unknown {
  if (typeof u === 'string') return u.startsWith('utc') ? u : `utc${u}`;
  if (isRecord(u) && typeof u.unit === 'string') return { ...u, unit: utcUnit(u.unit) };
  return u;
}
function utcDef(def: unknown, channel: string): unknown {
  if (Array.isArray(def)) return def.map((d) => utcDef(d, channel));
  if (!isRecord(def) || def.type !== 'temporal') return def;
  if (def.timeUnit !== undefined) return { ...def, timeUnit: utcUnit(def.timeUnit) };
  if (POSITIONAL.has(channel)) return { ...def, scale: { type: 'utc', ...(isRecord(def.scale) ? def.scale : {}) } };
  if (channel === 'tooltip') return { ...def, timeUnit: 'utcyearmonthdate' };
  return def;
}
export function utcDates(spec: Record<string, unknown>): Record<string, unknown> {
  const out: Record<string, unknown> = { ...spec };
  if (isRecord(spec.encoding)) out.encoding = Object.fromEntries(Object.entries(spec.encoding).map(([ch, d]) => [ch, utcDef(d, ch)]));
  if (Array.isArray(spec.layer)) out.layer = spec.layer.map((l) => (isRecord(l) ? utcDates(l) : l));
  return out;
}
