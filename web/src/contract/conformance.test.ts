import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { checkStream } from './conformance';
import { STREAM_RULES } from './streamRules.gen';

const API = resolve(__dirname, '../../../api/contract');
const cases = JSON.parse(readFileSync(resolve(API, 'conformance_cases/cases.json'), 'utf8')) as { name: string; events: unknown[]; expect: string[] }[];
const examples = readdirSync(resolve(API, 'examples')).filter((f) => f.endsWith('.jsonl'));

describe('stream conformance (shared rules, 1.12.0)', () => {
  it.each(cases.map((c) => [c.name, c] as const))('case %s matches the python runner', (_, c) => {
    expect(checkStream(c.events).map((x) => x.rule).sort()).toEqual(c.expect);
  });
  it.each(examples)('golden example %s conforms', (f) => {
    const evs = readFileSync(resolve(API, 'examples', f), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l) as unknown);
    expect(checkStream(evs)).toEqual([]);
  });
  it('every rule kind has a runner and a case that trips it', () => {
    const tripped = new Set(cases.flatMap((c) => c.expect));
    for (const r of STREAM_RULES.rules) expect(tripped.has(r.id)).toBe(true);
  });
  it('never throws on garbage', () => {
    for (const g of [null, 1, 'x', {}, [null], [[]], [{ type: 1 }], [{ type: 'part', part: null }], [{ type: 'message.end', finishReason: {} }]]) {
      expect(() => checkStream(g)).not.toThrow();
    }
  });
});
