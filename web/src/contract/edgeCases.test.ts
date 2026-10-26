import { readFileSync, readdirSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent } from '../api/contract';
import { checkStream } from './conformance';
import { EDGE_SCENARIOS } from './edgeCases';

const EDGE = resolve(__dirname, '../../../api/contract/examples/edge');
const load = (name: string): StreamEvent[] =>
  readFileSync(resolve(EDGE, `${name}.jsonl`), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l) as StreamEvent);
const view = (evs: StreamEvent[]): AssistantViewV1 => evs.reduce(reduceV1, newAssistantViewV1('m'));
const kinds = (v: AssistantViewV1) => v.parts.map((p) => p.type);

describe('1.16.0 edge-case examples', () => {
  it('every scenario has a recorded stream and every file is listed', () => {
    const files = readdirSync(EDGE).filter((f) => f.endsWith('.jsonl')).map((f) => f.replace('.jsonl', ''));
    const names = EDGE_SCENARIOS.map((s) => s.scenario as string);
    expect(files.filter((f) => !/_\d$/.test(f)).sort()).toEqual([...names].sort());
  });
  it.each(EDGE_SCENARIOS.map((s) => [s.scenario, s] as const))('%s conforms and reduces to its finish', (name, s) => {
    const evs = load(name);
    expect(checkStream(evs)).toEqual([]);
    const v = view(evs);
    expect(v.finishReason).toBe(s.finish);
    expect(v.streaming).toBe(false);
  });
  it.each(['clarify_nested_2', 'clarify_nested_3'])('%s (resume chain) conforms', (name) => {
    expect(checkStream(load(name))).toEqual([]);
  });
  it('cancel at every stage leaves no step running and no error part', () => {
    for (const s of EDGE_SCENARIOS.filter((x) => x.scenario.startsWith('cancel_'))) {
      const v = view(load(s.scenario));
      expect(v.steps.filter((st) => st.kind === 'sql' && st.state === 'running'), s.scenario).toEqual([]);
      expect(kinds(v), s.scenario).not.toContain('error');
    }
  });
  it('empty, multi-table, long SQL, many repairs and nested clarify reduce to the right parts', () => {
    const empty = view(load('empty'));
    const t = empty.parts.find((p) => p.type === 'table');
    expect(t && t.type === 'table' && t.rows.length).toBe(0);
    expect(kinds(empty)).not.toContain('chart');
    expect(kinds(view(load('multi_table'))).filter((k) => k === 'table')).toHaveLength(2);
    const code = view(load('long_sql')).parts.find((p) => p.type === 'code');
    expect(code && code.type === 'code' && code.code.length).toBeGreaterThan(4000);
    expect(view(load('many_repairs')).steps.filter((st) => st.kind === 'repair').length).toBeGreaterThanOrEqual(4);
    expect(kinds(view(load('clarify_nested_2')))).toContain('clarification');
    expect(kinds(view(load('clarify_nested_3')))).toContain('table');
  });
  it('escalation_abstain records the escalation and ends with an abstain part', () => {
    const v = view(load('escalation_abstain'));
    expect(v.escalations).toHaveLength(1);
    expect(kinds(v)).toContain('abstain');
  });
});
