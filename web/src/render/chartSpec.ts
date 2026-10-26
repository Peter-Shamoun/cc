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
