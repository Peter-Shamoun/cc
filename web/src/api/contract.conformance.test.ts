/**
 * Contract-v1 conformance for the client reducer (reduceV1):
 *  1. every event type in the contract has a typed reducer path and a unit test (the CASES table is
 *     typed by StreamEvent['type'], so a new event type fails `tsc` until it gets a test here);
 *  2. every canonical example stream (api/contract/examples/*.jsonl) and every mock demo fixture
 *     replays through the reducer to a consistent final view, also when replayed twice (resume).
 */
import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  EVENT_TYPES, isStepEvent, newAssistantViewV1, reduceV1,
  type AssistantViewV1, type EscalationEvent, type StreamEvent, type Usage,
} from './contract';
import { MockAnalystAdapter } from '../adapters/mock';
import { FIXTURES, pickFixture } from '../fixtures';
import { ANSWER_FIXTURES } from '../fixtures/answer';
import { V1_FIXTURES } from '../fixtures/v1';
import { hydrate } from '../adapters/httpStore';

type Ev = StreamEvent | { type: string; seq?: number };
const run = (evs: Ev[], from = newAssistantViewV1('a_local')) => evs.reduce<AssistantViewV1>((v, e) => reduceV1(v, e), from);
let n = 0;
const s = <T extends object>(e: T) => ({ ...e, seq: n++ }) as unknown as StreamEvent;
const start = () => { n = 0; return s({ type: 'message.start', messageId: 'm1', conversationId: 'c1', contractVersion: '1.0.0' }); };

const HAIKU_TO_SONNET: Omit<EscalationEvent, 'seq'> = {
  type: 'escalation', from: { model: 'claude-haiku-5-5', tier: 'small' }, to: { model: 'claude-sonnet-5-5', tier: 'medium' },
  trigger: 'repair_exhausted', reason: 'The smaller model could not produce a working query.',
};
const RICH_USAGE: Usage = {
  queries: 4, elapsedMs: 2149, costUsd: 0.013691, costIsEstimate: true, tokens: { input: 6633, output: 268 },
  latency: { totalMs: 2149, modelMs: 2101, sqlMs: 31, firstEventMs: 0, firstStepMs: 358, firstTokenMs: 2149 },
  models: [
    { model: 'claude-haiku-5-5', role: 'analyst', inputTokens: 4589, outputTokens: 105, costUsd: 0.005114, calls: 4 },
    { model: 'claude-sonnet-5-5', role: 'analyst', inputTokens: 2044, outputTokens: 163, costUsd: 0.008577, calls: 2 },
  ],
};

// ------------------------------------------------------------------ 1. one test per event type

const CASES: { [K in StreamEvent['type']]: () => void } = {
  'message.start': () => {
    const v = run([start()]);
    expect(v).toMatchObject({ id: 'm1', streaming: true, contractVersion: '1.0.0', lastSeq: 0 });
  },
  status: () => {
    const v = run([start(), s({ type: 'status', label: 'Running query' })]);
    expect(v.status).toBe('Running query');
    expect(run([s({ type: 'status', label: null })], v).status).toBeNull();
  },
  'step.sql_running': () => {
    const v = run([start(), s({ type: 'step.sql_running', stepId: 's1', sql: 'SELECT 1', purpose: 'answer', attempt: 1 })]);
    expect(v.steps).toEqual([expect.objectContaining({ stepId: 's1', kind: 'sql', state: 'running' })]);
  },
  'step.sql_result': () => {
    const ok = run([start(), s({ type: 'step.sql_running', stepId: 's1', sql: 'SELECT 1', purpose: 'answer', attempt: 1 }), s({ type: 'step.sql_result', stepId: 's1', ok: true, elapsedMs: 8, rowCount: 3, columns: [{ name: 'n' }] })]);
    expect(ok.steps).toHaveLength(1);
    expect(ok.steps[0]).toMatchObject({ kind: 'sql', state: 'ok' });
    expect(ok.steps[0].events.map((e) => e.type)).toEqual(['step.sql_running', 'step.sql_result']);
    const bad = run([s({ type: 'step.sql_result', stepId: 's2', ok: false, elapsedMs: 3, error: { code: 'binder_error', message: 'no column' } })], ok);
    expect(bad.steps[1]).toMatchObject({ stepId: 's2', state: 'error' });
  },
  'step.repair_attempt': () => {
    const v = run([start(), s({ type: 'step.repair_attempt', stepId: 's3', failedStepId: 's2', attempt: 1, maxAttempts: 3, error: { code: 'binder_error', message: 'x' }, revisedSql: 'SELECT 2' })]);
    expect(v.steps[0]).toMatchObject({ stepId: 's3', kind: 'repair', state: 'ok' });
  },
  'step.check': () => {
    const verdict = (vd: 'pass' | 'warn' | 'fail') => run([start(), s({ type: 'step.check', stepId: 'k', check: 'grain', verdict: vd, message: 'm' })]).steps[0].state;
    expect([verdict('pass'), verdict('warn'), verdict('fail')]).toEqual(['ok', 'warn', 'error']);
  },
  'step.explore': () => {
    const v = run([start(), s({ type: 'step.explore', stepId: 'e', probe: 'describe_view', target: 'sem.claims', summary: '8 columns' })]);
    expect(v.steps[0]).toMatchObject({ kind: 'explore', state: 'ok' });
  },
  'step.candidates': () => {
    const c = (chosenId: string | null) => run([start(), s({ type: 'step.candidates', stepId: 'c', candidates: [{ candidateId: 'a', sql: 'SELECT 1', ok: true }], chosenId, method: 'vote' })]).steps[0];
    expect(c('a')).toMatchObject({ kind: 'candidates', state: 'ok' });
    expect(c(null).state).toBe('warn');
  },
  escalation: () => {
    const v = run([start(), s(HAIKU_TO_SONNET)]);
    expect(v.escalations).toHaveLength(1);
    expect(v.escalations[0]).toMatchObject({ from: { model: 'claude-haiku-5-5' }, to: { model: 'claude-sonnet-5-5' }, trigger: 'repair_exhausted' });
    expect(v.steps[0]).toMatchObject({ stepId: 'esc-0', kind: 'escalation' });
    // with a stepId it lands on that step's timeline row
    const w = run([start(), s({ ...HAIKU_TO_SONNET, stepId: 'k1' })]);
    expect(w.steps[0].stepId).toBe('k1');
  },
  'text.start': () => {
    const v = run([start(), s({ type: 'status', label: 'Writing' }), s({ type: 'text.start', partId: 't' })]);
    expect(v.parts).toEqual([{ type: 'text', id: 't', text: '' }]);
    expect(v.openText).toEqual(['t']);
    expect(v.status).toBeNull();
  },
  'text.delta': () => {
    const v = run([start(), s({ type: 'text.start', partId: 't' }), s({ type: 'text.delta', partId: 't', delta: 'Denials ' }), s({ type: 'text.delta', partId: 't', delta: 'fell.' })]);
    expect(v.parts[0]).toMatchObject({ type: 'text', text: 'Denials fell.' });
    // a delta without text.start (resume mid-part) still opens the part
    expect(run([start(), s({ type: 'text.delta', partId: 'u', delta: 'x' })]).openText).toEqual(['u']);
  },
  'text.end': () => {
    const v = run([start(), s({ type: 'text.start', partId: 't' }), s({ type: 'text.delta', partId: 't', delta: 'a' }), s({ type: 'text.end', partId: 't' })]);
    expect(v.openText).toEqual([]);
    expect(v.parts[0]).toMatchObject({ text: 'a' });
  },
  'reasoning.delta': () => {
    const v = run([start(), s({ type: 'reasoning.delta', partId: 'r', delta: 'Checking ' }), s({ type: 'reasoning.delta', partId: 'r', delta: 'the view.' })]);
    expect(v.parts[0]).toMatchObject({ type: 'reasoning', summary: 'Checking the view.' });
  },
  'reasoning.end': () => {
    const v = run([start(), s({ type: 'reasoning.delta', partId: 'r', delta: 'x' }), s({ type: 'reasoning.end', partId: 'r', durationMs: 1200 })]);
    expect(v.parts[0]).toMatchObject({ type: 'reasoning', durationMs: 1200 });
  },
  'code.delta': () => {
    const v = run([start(), s({ type: 'code.delta', partId: 'q', delta: 'SELECT ' }), s({ type: 'code.delta', partId: 'q', delta: '1' })]);
    expect(v.parts[0]).toMatchObject({ type: 'code', code: 'SELECT 1' });
  },
  'table.start': () => {
    const cols = [{ key: 'payer', label: 'Payer', type: 'string' }];
    const v = run([start(), s({ type: 'table.start', partId: 't', columns: cols, title: 'Result' })]);
    expect(v.parts[0]).toMatchObject({ type: 'table', id: 't', rows: [], rowCount: 0, title: 'Result' });
    expect(v.streamingTables).toEqual(['t']);
    // aborted before the complete part: the partial table is dropped
    const ended = run([s({ type: 'message.end', messageId: 'm1', finishReason: 'aborted', usage: { queries: 0, elapsedMs: 1 } })], v);
    expect(ended.parts).toEqual([]);
  },
  'table.rows': () => {
    const cols = [{ key: 'payer', label: 'Payer', type: 'string' }];
    const v = run([start(), s({ type: 'table.start', partId: 't', columns: cols }), s({ type: 'table.rows', partId: 't', offset: 0, rows: [['A'], ['B']] }),
      s({ type: 'table.rows', partId: 't', offset: 1, rows: [['B'], ['C']] })]);
    expect(v.parts[0]).toMatchObject({ rows: [['A'], ['B'], ['C']], rowCount: 3 });
    const done = run([s({ type: 'part', part: { type: 'table', id: 't', columns: cols, rows: [['A'], ['B'], ['C']], rowCount: 80 } })], v);
    expect(done.parts).toHaveLength(1);
    expect(done.parts[0]).toMatchObject({ rowCount: 80 });
    expect(done.streamingTables).toEqual([]);
    expect(run([s({ type: 'table.rows', partId: 'zz', offset: 0, rows: [['x']] })], done).parts).toEqual(done.parts);
  },
  'chart.start': () => {
    const plot = { v: 1, kind: 'bar', title: 'Paid by payer', x: { field: 'payer', type: 'nominal', title: 'Payer' }, y: { field: 'paid', type: 'quantitative', title: 'Paid' } };
    const v = run([start(), s({ type: 'chart.start', partId: 'ch', plot, tablePartId: 't' })]);
    expect(v.parts[0]).toMatchObject({ type: 'chart', id: 'ch', plot: { ...plot, data: [] }, tablePartId: 't' });
    expect(v.streamingCharts).toEqual(['ch']);
    const bad = run([start(), s({ type: 'chart.start', partId: 'ch', plot: { ...plot, kind: 'pie' } })]);
    expect(bad.parts).toEqual([]);
    expect(bad.rejectedCharts).toEqual([{ partId: 'ch', rules: ['plot.kind'] }]);
  },
  'chart.points': () => {
    const plot = { v: 1, kind: 'bar', title: 'Paid by payer', x: { field: 'payer', type: 'nominal', title: 'Payer' }, y: { field: 'paid', type: 'quantitative', title: 'Paid' } };
    const v = run([start(), s({ type: 'chart.start', partId: 'ch', plot }), s({ type: 'chart.points', partId: 'ch', offset: 0, rows: [{ payer: 'A', paid: 1 }, { payer: 'B', paid: 2 }] }),
      s({ type: 'chart.points', partId: 'ch', offset: 1, rows: [{ payer: 'B', paid: 2 }, { payer: 'C', paid: 3 }] })]);
    expect(v.parts[0]).toMatchObject({ plot: { data: [{ payer: 'A', paid: 1 }, { payer: 'B', paid: 2 }, { payer: 'C', paid: 3 }] } });
    const ended = run([s({ type: 'message.end', messageId: 'm1', finishReason: 'aborted', usage: { queries: 0, elapsedMs: 1 } })], v);
    expect(ended.parts).toEqual([]);
  },
  part: () => {
    const v = run([start(), s({ type: 'part', part: { type: 'callout', id: 'c', tone: 'info', body: 'a' } }), s({ type: 'part', part: { type: 'callout', id: 'c', tone: 'warning', body: 'b' } })]);
    expect(v.parts).toHaveLength(1);
    expect(v.parts[0]).toMatchObject({ tone: 'warning', body: 'b' });
  },
  'part.patch': () => {
    const v = run([start(), s({ type: 'part', part: { type: 'tool_call', id: 'tc', tool: 'run_sql', label: 'q', state: 'running' } }), s({ type: 'part.patch', partId: 'tc', patch: { state: 'ok', durationMs: 40 } })]);
    expect(v.parts[0]).toMatchObject({ id: 'tc', state: 'ok', durationMs: 40 });
  },
  'message.end': () => {
    const v = run([start(), s({ type: 'step.sql_running', stepId: 's1', sql: 'SELECT 1', purpose: 'answer', attempt: 1 }), s({ type: 'text.start', partId: 't' }), s({ type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: RICH_USAGE })]);
    expect(v).toMatchObject({ streaming: false, status: null, openText: [], finishReason: 'stop' });
    expect(v.steps[0].state).toBe('ok'); // running steps close
    // cost / latency per answer survive as-is
    expect(v.usage).toEqual(RICH_USAGE);
    const stopped = run([start(), s({ type: 'step.sql_running', stepId: 's1', sql: 'SELECT 1', purpose: 'answer', attempt: 1 }), s({ type: 'message.end', messageId: 'm1', finishReason: 'aborted', usage: { queries: 0, elapsedMs: 5 } })]);
    expect(stopped.steps[0].state).toBe('error'); // a stopped query is not shown as succeeded
  },
  heartbeat: () => {
    const v = run([start()]);
    expect(reduceV1(v, { type: 'heartbeat', at: '2026-10-25T00:00:00Z' })).toBe(v);
  },
};

describe('reduceV1: one typed path per contract event', () => {
  it('the cases table covers EVENT_TYPES exactly', () => {
    expect(Object.keys(CASES).sort()).toEqual([...EVENT_TYPES].sort());
  });
  for (const [type, fn] of Object.entries(CASES)) it(type, fn);

  it('forward compatible: unknown event types and part types do not throw', () => {
    const v = run([start(), ({ type: 'step.future_thing', stepId: 'z', seq: 1 } as Ev), s({ type: 'part', part: { type: 'hologram', id: 'h' } as never })]);
    expect(v.streaming).toBe(true);
    expect(v.lastSeq).toBe(n - 1);
  });
});

// ------------------------------------------------------------------ 2. replay every fixture

/** Invariants every complete stream must satisfy once reduced. */
function checkReplay(evs: Ev[], label: string) {
  const typed = evs as StreamEvent[];
  for (const e of typed) expect(EVENT_TYPES as readonly string[], `${label}: unknown event ${e.type}`).toContain(e.type);
  const seqs = typed.filter((e) => 'seq' in e).map((e) => (e as { seq: number }).seq);
  expect(seqs, `${label}: seq strictly increasing`).toEqual([...seqs].sort((a, b) => a - b));
  expect(new Set(seqs).size).toBe(seqs.length);
  expect(typed[0].type).toBe('message.start');
  const end = typed.at(-1)!;
  expect(end.type, `${label}: ends with message.end`).toBe('message.end');

  const base = newAssistantViewV1('a_local');
  const v = run(evs, base);
  if (end.type !== 'message.end' || typed[0].type !== 'message.start') return;
  expect(v.id).toBe(typed[0].messageId);
  expect(v).toMatchObject({ streaming: false, status: null, openText: [], finishReason: end.finishReason });
  expect(v.usage).toEqual(end.usage);
  expect(v.lastSeq).toBe(seqs.at(-1));

  // every step id is on the timeline, in first-seen order, and nothing is left running
  const stepIds = [...new Set(typed.filter(isStepEvent).map((e) => e.stepId))];
  for (const id of stepIds) expect(v.steps.map((x) => x.stepId), `${label}: step ${id}`).toContain(id);
  expect(v.steps.filter((x) => x.state === 'running'), `${label}: running steps after end`).toEqual([]);
  const stepEventCount = typed.filter((e) => isStepEvent(e) || e.type === 'escalation').length;
  expect(v.steps.reduce((k, x) => k + x.events.length, 0)).toBe(stepEventCount);
  expect(v.escalations).toHaveLength(typed.filter((e) => e.type === 'escalation').length);

  // every part / text / reasoning / code id becomes exactly one part
  const partIds = new Set<string>();
  for (const e of typed) {
    if (e.type === 'part') partIds.add(e.part.id);
    if (e.type === 'text.start' || e.type === 'text.delta' || e.type === 'reasoning.delta' || e.type === 'code.delta') partIds.add(e.partId);
  }
  expect(new Set(v.parts.map((p) => p.id))).toEqual(partIds);
  for (const e of typed) if (e.type === 'text.delta') expect(v.parts.find((p) => p.id === e.partId)).toHaveProperty('text', expect.stringContaining(e.delta));

  // resume: replaying the whole stream again (Last-Event-ID from 0) changes nothing
  const twice = run(evs, v);
  expect(twice).toBe(v);
  // resume mid-stream: a prefix, then a replay that overlaps it, ends identical
  const cut = Math.floor(evs.length / 2);
  expect(run(evs.slice(Math.max(0, cut - 3)), run(evs.slice(0, cut), base))).toEqual(v);
  return v;
}

// vitest runs from web/ (jsdom rewrites import.meta.url, so resolve from the package root)
const EXAMPLES = path.resolve(process.cwd(), '../api/contract/examples');
const exampleFiles = fs.readdirSync(EXAMPLES).filter((f) => f.endsWith('.jsonl')).sort();
const readExample = (f: string) => fs.readFileSync(path.join(EXAMPLES, f), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l) as Ev);

describe('conformance: api/contract/examples replay through reduceV1', () => {
  it('found the canonical examples', () => {
    expect(exampleFiles.length).toBeGreaterThanOrEqual(9);
  });
  for (const f of exampleFiles) it(f, () => { checkReplay(readExample(f), f); });

  it('escalation.jsonl: Haiku → Sonnet with cost and latency per answer and per model', () => {
    const v = checkReplay(readExample('escalation.jsonl'), 'escalation')!;
    expect(v.escalations[0]).toMatchObject({ from: { model: expect.stringContaining('haiku') }, to: { model: expect.stringContaining('sonnet') } });
    const u = v.usage!;
    expect(u.costUsd).toBeGreaterThan(0);
    expect(u.costIsEstimate).toBe(true);
    expect(u.latency?.totalMs).toBeGreaterThan(0);
    expect(u.models?.map((m) => m.model)).toEqual(expect.arrayContaining([v.escalations[0].from.model, v.escalations[0].to.model]));
    const perModel = u.models!.reduce((k, m) => k + (m.costUsd ?? 0), 0);
    expect(perModel).toBeCloseTo(u.costUsd!, 5);
  });

  it('a reloaded transcript (steps saved as raw events) rebuilds the same timeline', () => {
    const evs = readExample('escalation.jsonl') as StreamEvent[];
    const live = run(evs);
    const end = evs.at(-1) as Extract<StreamEvent, { type: 'message.end' }>;
    const saved = { id: live.id, role: 'assistant', createdAt: live.createdAt, parts: live.parts, steps: evs.filter((e) => isStepEvent(e) || e.type === 'escalation'), usage: end.usage, finishReason: end.finishReason };
    const back = hydrate(saved as never) as unknown as AssistantViewV1;
    expect(back.steps.map((x) => [x.stepId, x.kind, x.state])).toEqual(live.steps.map((x) => [x.stepId, x.kind, x.state]));
    expect(back.escalations).toEqual(live.escalations);
    expect(back.usage).toEqual(live.usage);
  });
});

describe('conformance: every mock demo fixture replays through reduceV1', () => {
  const all = [...FIXTURES, ...ANSWER_FIXTURES, ...V1_FIXTURES].filter((f) => pickFixture(f.prompt) === f);
  it('covers the demo fixtures', () => { expect(all.length).toBeGreaterThanOrEqual(10); });
  for (const f of all) {
    it(f.id, async () => {
      const evs: Ev[] = [];
      for await (const e of new MockAnalystAdapter(0).streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: f.prompt }], signal: new AbortController().signal })) evs.push(e as Ev);
      const v = checkReplay(evs, f.id)!;
      expect(v.finishReason).toBe(f.finishReason ?? 'stop');
      expect(v.usage?.queries).toBe(f.queries);
    });
  }
});
