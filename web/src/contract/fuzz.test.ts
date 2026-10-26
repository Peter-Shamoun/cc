/**
 * Property/fuzz tests (contract 1.12.0): the v1 reducer and the SSE decoder fail safe on malformed, duplicated and
 * out-of-order input. Seeded PRNG: a failure prints its seed, so it is reproducible.
 */
import { readdirSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { newAssistantViewV1, reduceV1, reduceV1Strict, wellFormed, type AssistantViewV1 } from '../api/contract';
import { checkStream } from './conformance';
import { SseDecoder, parseSseText } from './sse';

const EX = resolve(__dirname, '../../../api/contract/examples');
const STREAMS: Record<string, Record<string, unknown>[]> = Object.fromEntries(
  readdirSync(EX).filter((f) => f.endsWith('.jsonl')).map((f) => [f,
    readFileSync(resolve(EX, f), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l) as Record<string, unknown>)]),
);
const ALL_EVENTS = Object.values(STREAMS).flat();
const RUNS = 300;

function rng(seed: number) {
  let s = seed >>> 0 || 1;
  const next = () => { s ^= s << 13; s >>>= 0; s ^= s >>> 17; s ^= s << 5; s >>>= 0; return s / 4294967296; };
  return { next, int: (n: number) => Math.floor(next() * n), pick: <T,>(a: readonly T[]): T => a[Math.floor(next() * a.length)] };
}
type R = ReturnType<typeof rng>;

const BASE: AssistantViewV1 = { ...newAssistantViewV1('m'), createdAt: '2026-10-25T20:00:00Z' };
const fold = (evs: unknown[], reducer = reduceV1, m: AssistantViewV1 = BASE) =>
  evs.reduce<AssistantViewV1>((acc, e) => reducer(acc, e as never), m);

const JUNK: unknown[] = [null, undefined, 0, -1, 1.5, NaN, Infinity, '', 'x', true, [], {}, [[]], { id: 1 }, { type: 'table' },
  { id: 'p', type: 'table', rows: null, columns: null }, '__proto__', { __proto__: { polluted: 1 } }];

/** Corrupt one event: drop, retype or junk a field, or replace it entirely. */
function mutate(r: R, e: Record<string, unknown>): unknown {
  const keys = Object.keys(e);
  switch (r.int(6)) {
    case 0: return r.pick(JUNK);
    case 1: { const c = { ...e }; delete c[r.pick(keys)]; return c; }
    case 2: return { ...e, [r.pick(keys)]: r.pick(JUNK) };
    case 3: return { ...e, type: r.pick(ALL_EVENTS).type };
    case 4: return { ...e, seq: r.pick([-1, 1.5, '3', null, 1e12]) };
    default: return isPart(e) ? { ...e, part: { ...(e.part as object), [r.pick(['id', 'type', 'rows', 'columns', 'items', 'text'])]: r.pick(JUNK) } } : e;
  }
}
const isPart = (e: Record<string, unknown>) => e.type === 'part' && typeof e.part === 'object';

describe('reducer fuzz (fails safe)', () => {
  it('never throws on corrupted events, and ignores them structurally', () => {
    for (let seed = 1; seed <= RUNS; seed++) {
      const r = rng(seed);
      const src = r.pick(Object.values(STREAMS));
      const evs = src.map((e) => (r.next() < 0.25 ? mutate(r, e) : e));
      expect(() => fold(evs), `seed ${seed}`).not.toThrow();
      const v = fold(evs);
      expect(Array.isArray(v.parts) && Array.isArray(v.steps), `seed ${seed}`).toBe(true);
      for (const p of v.parts) expect(typeof p.id === 'string' && typeof p.type === 'string', `seed ${seed}`).toBe(true);
    }
  });

  it('the strict core never throws on well-formed events in any order (no reliance on the safety net)', () => {
    for (let seed = 1; seed <= RUNS; seed++) {
      const r = rng(seed);
      const evs = Array.from({ length: 5 + r.int(40) }, () => ({ ...r.pick(ALL_EVENTS), seq: r.int(60) }));
      expect(evs.every(wellFormed)).toBe(true);
      expect(() => fold(evs, reduceV1Strict), `seed ${seed}`).not.toThrow();
    }
  });

  it.each(Object.keys(STREAMS))('%s: replay, resume and heartbeats do not change the view', (name) => {
    const evs = STREAMS[name];
    const want = fold(evs);
    expect(fold([...evs, ...evs])).toEqual(want); // a full replay is dropped by seq
    const r = rng(name.length);
    for (let k = 0; k < 20; k++) {
      const cut = r.int(evs.length);
      expect(fold([...evs.slice(0, cut), ...evs.slice(r.int(cut + 1))])).toEqual(want); // resume from any earlier seq
      const hb = evs.flatMap((e) => (r.next() < 0.3 ? [{ type: 'heartbeat' }, e] : [e]));
      expect(fold(hb)).toEqual(want);
    }
    expect(want.streaming).toBe(false);
    expect(want.openText).toEqual([]);
    expect(want.streamingTables ?? []).toEqual([]);
  });

  it('out-of-order delivery equals the in-order prefix of what arrived (late events are dropped)', () => {
    for (let seed = 1; seed <= RUNS; seed++) {
      const r = rng(seed);
      const evs = r.pick(Object.values(STREAMS)).slice();
      for (let k = 0; k < 3; k++) { const i = r.int(evs.length); const j = r.int(evs.length); [evs[i], evs[j]] = [evs[j], evs[i]]; }
      let max = -1;
      const accepted = evs.filter((e) => { const s = e.seq as number; if (s > max) { max = s; return true; } return false; });
      expect(fold(evs), `seed ${seed}`).toEqual(fold(accepted));
      const swapped = evs.some((e, i) => i > 0 && (e.seq as number) < (evs[i - 1].seq as number));
      if (swapped) expect(checkStream(evs).length, `seed ${seed}`).toBeGreaterThan(0);
    }
  });

  it('the strict core survives events that pass wellFormed after corruption', () => {
    for (let seed = 1; seed <= RUNS; seed++) {
      const r = rng(seed);
      const src = r.pick(Object.values(STREAMS));
      const evs = src.map((e) => (r.next() < 0.3 ? mutate(r, e) : e)).filter(wellFormed);
      expect(() => fold(evs, reduceV1Strict), `seed ${seed}`).not.toThrow();
    }
  });

  it('drops wrong kinds and missing ids, tolerates missing scalars', () => {
    expect(wellFormed({ type: 'step.sql_result', stepId: 's', ok: 'yes', elapsedMs: 1 })).toBe(false);
    expect(wellFormed({ type: 'step.sql_result', ok: true, elapsedMs: 1 })).toBe(false);
    expect(wellFormed({ type: 'step.sql_result', stepId: 's', elapsedMs: 1 })).toBe(true);
    expect(wellFormed({ type: 'table.rows', partId: 'p', offset: 0, rows: [1] })).toBe(false);
    expect(wellFormed({ type: 'part', part: { id: 1, type: 'text' } })).toBe(false);
    expect(wellFormed({ type: 'text.delta', partId: 'p' })).toBe(false);
    expect(wellFormed({ type: 'some.future.event' })).toBe(true);
    expect(wellFormed({ type: 'heartbeat', seq: -1 })).toBe(false);
  });

  it('never pollutes prototypes', () => {
    fold([{ type: 'part', seq: 0, part: JSON.parse('{"id":"p","type":"text","text":"x","__proto__":{"polluted":1}}') }]);
    expect(({} as Record<string, unknown>).polluted).toBeUndefined();
  });
});

const sse = (evs: unknown[]) => evs.map((e, i) => `id: m:${i}\ndata: ${JSON.stringify(e)}\n\n`).join('');

describe('SSE decoder fuzz (fails safe)', () => {
  it.each(Object.keys(STREAMS))('%s: any chunking and CRLF decode to the same events', (name) => {
    const text = sse(STREAMS[name]);
    const r = rng(name.length * 7);
    for (let k = 0; k < 25; k++) {
      const body = r.next() < 0.5 ? text.replace(/\n/g, '\r\n') : text;
      const d = new SseDecoder();
      const out = [];
      for (let i = 0; i < body.length;) { const n = 1 + r.int(40); out.push(...d.push(body.slice(i, i + n))); i += n; }
      out.push(...d.end());
      expect(out.map((f) => f.event)).toEqual(STREAMS[name]);
      expect(d.malformed).toBe(0);
    }
  });

  it('skips and counts malformed frames without losing the good ones', () => {
    for (let seed = 1; seed <= RUNS; seed++) {
      const r = rng(seed);
      const good = r.pick(Object.values(STREAMS));
      const bad = ['data: {not json\n\n', 'data: 42\n\n', 'data: null\n\n', 'data: [1]\n\n', 'data: {"type":7}\n\n', 'data:\n\n'];
      const frames: string[] = [];
      let nBad = 0;
      for (const e of good) {
        if (r.next() < 0.2) { const b = r.pick(bad); frames.push(b); if (b !== 'data:\n\n') nBad += 1; }
        if (r.next() < 0.1) frames.push(': keep-alive\n\n');
        frames.push(`data: ${JSON.stringify(e)}\n\n`);
      }
      const { frames: got, malformed } = parseSseText(frames.join(''));
      expect(got.map((f) => f.event), `seed ${seed}`).toEqual(good);
      // `data:` with an empty value is a frame with empty data: malformed JSON, also counted
      expect(malformed, `seed ${seed}`).toBeGreaterThanOrEqual(nBad);
    }
  });

  it('never throws on random bytes', () => {
    for (let seed = 1; seed <= RUNS; seed++) {
      const r = rng(seed);
      const s = Array.from({ length: r.int(300) }, () => String.fromCharCode(r.pick([10, 13, 58, 32, 123, 125, 34, 100, 97, 116]) ?? 0) + (r.next() < 0.1 ? 'data: ' : '')).join('');
      expect(() => parseSseText(s), `seed ${seed}`).not.toThrow();
    }
  });
});
