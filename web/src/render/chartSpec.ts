import type { ChartPart } from '../lib/contract';

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
  const cleaned = (isRecord(part?.spec) ? sanitize(part.spec) : {}) as Record<string, unknown>;
  delete cleaned.data;
  const values = Array.isArray(part?.data?.values) ? part.data.values : [];
  return { ...cleaned, data: { values } };
}

/** Spec §4.5: SVG under 2k marks, canvas above that so a huge series cannot build a giant DOM. */
export function chartRenderer(part: ChartPart): 'svg' | 'canvas' {
  const n = part?.data?.values?.length ?? 0;
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
    const axis = x.axis === null ? null : { labelAngle: 0, labelLimit: 120, ...(x.axis ?? {}) };
    return { ...spec, encoding: { ...enc, x: { ...x, axis } } };
  }
  const sort = x.sort === '-y' ? '-x' : x.sort === 'y' ? 'x' : x.sort;
  const flipped: Record<string, unknown> = {
    ...enc,
    x: y.axis === null ? y : { ...y, axis: { tickCount: 6, ...(y.axis ?? {}) } },
    // The category axis title would sit rotated behind long labels; the chart title already names it.
    y: { ...x, title: null, ...(sort !== undefined ? { sort } : {}), axis: x.axis === null ? null : { labelLimit: 200, ...(x.axis ?? {}), labelAngle: 0 } },
  };
  const height = Math.min(Math.max(labels.size * 28, 140), 640);
  return { ...spec, encoding: flipped, height };
}
