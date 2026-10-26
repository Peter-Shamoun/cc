/**
 * Stream conformance runner (contract 1.12.0). It interprets `STREAM_RULES`, which is generated from
 * api/contract/stream_rules.json, the single source of the CONTRACT.md §2 ordering rules shared with
 * api/contract/conformance.py. Both runners are tested against api/contract/conformance_cases/cases.json.
 *
 *   checkStream(events)  // → [] when the stream conforms
 *
 * Pure and total: malformed input is reported as violations and never throws. Use it in tests and in dev-only
 * stream assertions. Never use it to block rendering: clients fail safe and keep rendering (§3).
 */
import { STREAM_RULES } from './streamRules.gen';

export interface Violation { rule: string; index: number; message: string }

type Ev = Record<string, unknown> & { type: string };
type Indexed = [number, Ev];
// The generated JSON is `as const`; runners read it through this loose shape.
type Rule = { id: string; kind: string } & Record<string, unknown>;
type Rules = { rules: readonly Rule[] };

export function get(ev: unknown, path: string): unknown {
  let cur: unknown = ev;
  for (const k of path.split('.')) {
    if (cur === null || typeof cur !== 'object' || Array.isArray(cur) || !Object.prototype.hasOwnProperty.call(cur, k)) return undefined;
    cur = (cur as Record<string, unknown>)[k];
  }
  return cur;
}

export const matches = (t: unknown, pattern: string): boolean =>
  typeof t === 'string' && (pattern.endsWith('.*') ? t.startsWith(pattern.slice(0, -1)) : t === pattern);
const any = (t: unknown, ps: readonly string[]) => ps.some((p) => matches(t, p));
const eq = (a: unknown, b: unknown) => a === b || (a === undefined && b === null) || (a === null && b === undefined);
const v = (rule: Rule, index: number, message: string): Violation => ({ rule: rule.id, index, message });
const str = (x: unknown) => JSON.stringify(x) ?? String(x);

type Runner = (r: Rule, evs: Indexed[], finish: unknown) => Violation[];

const RUNNERS: Record<string, Runner> = {
  first: (r, evs) => (evs.length && evs[0][1].type === r.type ? [] : [v(r, evs.length ? evs[0][0] : -1, `first event must be ${r.type}`)]),
  last: (r, evs) => (evs.length && evs[evs.length - 1][1].type === r.type ? [] : [v(r, evs.length ? evs[evs.length - 1][0] : -1, `last event must be ${r.type}`)]),
  count: (r, evs) => {
    const hits = evs.filter(([, e]) => matches(e.type, r.type as string)).map(([i]) => i);
    const min = (r.min as number) ?? 0;
    const max = (r.max as number) ?? Infinity;
    if (hits.length >= min && hits.length <= max) return [];
    return [v(r, hits.length > max ? hits[max] : -1, `${r.type} appears ${hits.length} times`)];
  },
  seq: (r, evs) => {
    let want = (r.start as number) ?? 0;
    for (const [i, e] of evs) {
      if (!Number.isInteger(e.seq) || e.seq !== want) return [v(r, i, `seq ${str(e.seq)}, expected ${want}`)];
      want += 1;
    }
    return [];
  },
  same: (r, evs) => {
    const vals = evs.filter(([, e]) => any(e.type, r.types as string[])).map(([i, e]) => [i, get(e, r.field as string)] as const);
    const bad = vals.slice(1).find(([, x]) => x !== vals[0][1]);
    return bad ? [v(r, bad[0], `${r.field} differs`)] : [];
  },
  enum: (r, evs) => evs
    .filter(([, e]) => matches(e.type, r.type as string) && !(r.values as unknown[]).includes(get(e, r.field as string)))
    .map(([i, e]) => v(r, i, `${r.field} ${str(get(e, r.field as string))} is not one of ${(r.values as string[]).join(', ')}`)),
  unique: (r, evs) => {
    const seen = new Set<string>();
    const out: Violation[] = [];
    for (const [i, e] of evs) {
      if (!any(e.type, r.types as string[])) continue;
      const x = get(e, r.field as string);
      if (typeof x !== 'string') out.push(v(r, i, `${r.field} missing`));
      else if (seen.has(x)) out.push(v(r, i, `${r.field} ${x} repeats`));
      if (typeof x === 'string') seen.add(x);
    }
    return out;
  },
  ref: (r, evs) => {
    const known = new Set<unknown>();
    const out: Violation[] = [];
    const where = Object.entries((r.where as Record<string, unknown>) ?? {});
    for (const [i, e] of evs) {
      if (matches(e.type, r.type as string)) {
        const x = get(e, r.field as string);
        if (x !== undefined && x !== null && !known.has(x)) out.push(v(r, i, `${r.field} ${str(x)} does not name an earlier ${r.target}`));
      }
      if (matches(e.type, r.target as string) && where.every(([k, want]) => eq(get(e, k), want))) {
        const t = get(e, r.targetField as string);
        if (typeof t === 'string') known.add(t);
      }
    }
    return out;
  },
  span: (r, evs, finish) => {
    const state = new Map<unknown, 'open' | 'closed'>();
    const out: Violation[] = [];
    const inner = r.inner as string[];
    const close = r.close as string[];
    for (const [i, e] of evs) {
      if (e.type === r.open) {
        const k = get(e, r.key as string);
        if (typeof k !== 'string' || state.has(k)) out.push(v(r, i, `${e.type} ${str(k)} opened twice or without a key`));
        else state.set(k, 'open');
      } else if (inner.includes(e.type)) {
        const k = get(e, r.key as string);
        if (state.get(k) !== 'open') out.push(v(r, i, `${e.type} ${str(k)} outside its ${r.open} … ${close[0]}`));
      } else if (close.includes(e.type)) {
        const k = get(e, r.closeKey as string);
        if (state.has(k)) {
          if (state.get(k) !== 'open') out.push(v(r, i, `${e.type} ${str(k)} closed twice`));
          state.set(k, 'closed');
        } else if (!r.closeWithoutOpenOk) out.push(v(r, i, `${e.type} ${str(k)} without ${r.open}`));
      }
    }
    if (!((r.unclosedOk as unknown[]) ?? []).includes(finish)) {
      for (const [k, s] of state) if (s === 'open') out.push(v(r, -1, `${r.open} ${str(k)} never closed`));
    }
    return out;
  },
  none_after: (r, evs) => {
    const exc = Object.entries((r.anchorExcept as Record<string, unknown[]>) ?? {});
    let started = false;
    const out: Violation[] = [];
    for (const [i, e] of evs) {
      if (!started && (r.anchor as string[]).includes(e.type) && !exc.some(([p, vs]) => vs.includes(get(e, p)))) {
        started = true;
        continue;
      }
      if (started && any(e.type, r.forbidden as string[]) && !any(e.type, (r.allowed as string[]) ?? [])) {
        out.push(v(r, i, `${e.type} after the answer began`));
      }
    }
    return out;
  },
  finish_requires: (r, evs, finish) => {
    if (finish !== r.finishReason) return [];
    return evs.some(([, e]) => e.type === 'part' && get(e, 'part.type') === r.partType)
      ? [] : [v(r, -1, `finishReason ${str(finish)} without a ${r.partType} part`)];
  },
};

export function checkStream(events: unknown, rules: Rules = STREAM_RULES as unknown as Rules): Violation[] {
  if (!Array.isArray(events)) return [{ rule: 'stream.list', index: -1, message: 'the stream is not a list of events' }];
  const out: Violation[] = [];
  const evs: Indexed[] = [];
  events.forEach((e: unknown, i) => {
    if (e === null || typeof e !== 'object' || Array.isArray(e) || typeof (e as { type?: unknown }).type !== 'string') {
      out.push({ rule: 'event.shape', index: i, message: 'an event must be an object with a string `type`' });
    } else if ((e as Ev).type !== 'heartbeat') evs.push([i, e as Ev]);
  });
  const end = [...evs].reverse().find(([, e]) => e.type === 'message.end');
  const finish = end ? get(end[1], 'finishReason') : undefined;
  for (const r of rules.rules) {
    const run = RUNNERS[r.kind];
    if (run) out.push(...run(r, evs, finish));
  }
  return out.sort((a, b) => a.index - b.index || (a.rule < b.rule ? -1 : a.rule > b.rule ? 1 : 0));
}
