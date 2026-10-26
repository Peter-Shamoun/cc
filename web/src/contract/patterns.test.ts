import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { PLOT_SCHEMA } from './v1.gen';

// 1.19.0: the shared pattern cases (api/contract/pattern_cases.json) also run in Python (api/tests/test_patterns_119.py).
// JSON Schema `pattern` is an ECMAScript regex, unanchored, so `.test` is the right semantics.
const API = resolve(__dirname, '../../../api/contract');
const schema = JSON.parse(readFileSync(resolve(API, 'v1.schema.json'), 'utf8')) as Record<string, unknown>;
const { cases } = JSON.parse(readFileSync(resolve(API, 'pattern_cases.json'), 'utf8')) as {
  cases: { path: string; ok: string[]; bad: string[] }[];
};

const patternAt = (path: string): string => {
  let node: unknown = schema;
  for (const k of path.split('.')) node = (node as Record<string, unknown>)[k];
  return typeof node === 'string' ? node : ((node as { pattern: string }).pattern);
};

describe('schema patterns agree with Python (pattern_cases.json)', () => {
  it.each(cases.map((c) => [c.path, c] as const))('%s', (_p, c) => {
    const re = new RegExp(patternAt(c.path));
    for (const s of c.ok) expect(re.test(s), JSON.stringify(s)).toBe(true);
    for (const s of c.bad) expect(re.test(s), JSON.stringify(s)).toBe(false);
  });

  it('the generated PLOT_SCHEMA patterns are the schema ones', () => {
    expect(PLOT_SCHEMA.fieldPattern).toBe(patternAt('$defs.PlotRow.propertyNames'));
    expect(PLOT_SCHEMA.temporalPattern).toBe(patternAt('$defs.PlotSpec.x-temporal'));
  });
});
