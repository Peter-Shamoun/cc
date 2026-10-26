import type { AssistantView, ToolCallPart } from '../../lib/contract';
import type { AssistantViewV1, EscalationEvent, Part as PartV1, StepEvent, StepView, Usage } from '../../api/contract';
import type { CheckResult, Candidate, Step, StepKind, UsageView } from './provisional';

/** Either message view: the legacy reducer's or the v1 reducer's. */
export type AnyView = AssistantView | AssistantViewV1;
export const partsOf = (m: AnyView) => m.parts as PartV1[];

const KIND: Record<string, StepKind> = {
  run_sql: 'sql', sql: 'sql', query: 'sql',
  repair: 'repair', repair_sql: 'repair', fix_sql: 'repair',
  verify: 'check', verifier: 'check', check: 'check', checks: 'check',
  explore: 'explore', probe: 'explore', explore_probe: 'explore',
  candidates: 'candidates', compare_candidates: 'candidates', select_candidate: 'candidates',
  make_chart: 'chart', describe_view: 'schema', list_views: 'schema', search_examples: 'search',
};

type Loose = ToolCallPart & { kind?: string; error?: string; previousSql?: string; attempt?: number; checks?: CheckResult[]; candidates?: Candidate[]; output?: ToolCallPart['output'] & { resultId?: string; checks?: CheckResult[]; candidates?: Candidate[] } };

/** Legacy `tool_call` part -> Step (still valid in v1, CONTRACT.md §5). */
export function toStep(p: ToolCallPart): Step {
  const l = p as Loose;
  const args = (p.input?.args ?? {}) as Record<string, unknown>;
  const checks = l.checks ?? l.output?.checks ?? (args.checks as CheckResult[] | undefined);
  return {
    id: p.id,
    kind: KIND[l.kind ?? p.tool] ?? 'other',
    label: p.label,
    // A finished check step with a failed check reads as a warning, not a clean pass.
    state: p.state === 'ok' && checks?.some((c) => !c.passed) ? 'warn' : p.state,
    sql: p.input?.sql,
    rowCount: p.output?.rowCount,
    durationMs: p.durationMs,
    message: p.output?.message,
    error: l.error ?? (args.error as string | undefined),
    previousSql: l.previousSql ?? (args.previousSql as string | undefined),
    attempt: l.attempt ?? (args.attempt as number | undefined),
    checks,
    candidates: l.candidates ?? l.output?.candidates ?? (args.candidates as Candidate[] | undefined),
    agent: p.agent,
    resultId: l.output?.resultId,
  };
}

// ------------------------------------------------------------------ v1 step events -> Step

const PROBE: Record<string, string> = {
  list_views: 'Listed views', describe_view: 'Read schema', sample_values: 'Sampled values', lookup_glossary: 'Looked up glossary',
  lookup_dictionary: 'Looked up data dictionary', search_examples: 'Searched examples', profile: 'Profiled',
};
const PURPOSE: Record<string, string> = { explore: 'Probed the data', answer: 'Ran query', candidate: 'Ran candidate query', repair: 'Re-ran repaired query', check: 'Ran check query' };
const PRESENT: Record<string, string> = { explore: 'Probing the data', answer: 'Running query', candidate: 'Running candidate query', repair: 'Re-running repaired query', check: 'Running check query' };
const CHECK: Record<string, string> = { verifier: 'Verifier', row_count: 'Row count', null_share: 'Null share', join_fanout: 'Join fan-out', date_range: 'Date range', definition: 'Metric definition' };
const humanize = (s: string) => (CHECK[s] ?? s.replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase()));
export const modelName = (m: string) => m.replace(/^claude-/, '').replace(/-\d{6,8}$/, '').replace(/(\d)-(\d)/g, '$1.$2').replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());

const PAST: [RegExp, string][] = [[/^Running\b/, 'Ran'], [/^Re-running\b/, 'Re-ran'], [/^Checking\b/, 'Checked'], [/^Exploring\b/, 'Explored'], [/^Probing\b/, 'Probed'], [/^Comparing\b/, 'Compared'], [/^Repairing\b/, 'Repaired'], [/^Fixing\b/, 'Fixed']];
/** Server labels are present tense ("Running query"); a finished row reads in past tense. */
export const pastTense = (l: string) => PAST.reduce((a, [re, to]) => a.replace(re, to), l);

const find = <T extends StepEvent['type']>(v: StepView, t: T) => v.events.find((e) => e.type === t) as Extract<StepEvent, { type: T }> | undefined;

function sqlOf(views: StepView[], stepId: string): string | undefined {
  const v = views.find((x) => x.stepId === stepId);
  return v && find(v, 'step.sql_running')?.sql;
}

function fromView(v: StepView, all: StepView[], finished: boolean, aborted: boolean): Step | null {
  const base = { id: v.stepId };
  const state: Step['state'] = v.state;
  switch (v.kind) {
    case 'sql': {
      const run = find(v, 'step.sql_running');
      const res = find(v, 'step.sql_result');
      const purpose = run?.purpose ?? 'answer';
      const done = !!res;
      const generic = !run?.label || /^(running|ran) (the )?query$/i.test(run.label);
      let label = generic ? (done ? PURPOSE : PRESENT)[purpose] ?? 'Ran query' : done ? pastTense(run!.label!) : run!.label!;
      if (purpose === 'candidate' && run?.candidateId) label = `Candidate ${run.candidateId}: ${generic ? (done ? 'ran' : 'running') : label}`;
      return {
        ...base, kind: purpose === 'explore' ? 'explore' : 'sql', label,
        state: !res && (aborted || !finished) ? (finished ? 'cancelled' : 'running') : state,
        sql: run?.sql, rowCount: res?.rowCount, truncated: res?.truncated, durationMs: res?.elapsedMs,
        columns: res?.columns?.map((c) => c.name), error: res?.error?.message,
        attempt: run && run.attempt > 1 ? run.attempt : undefined,
      };
    }
    case 'repair': {
      const r = find(v, 'step.repair_attempt');
      if (!r) return null;
      const rerun = all.find((x) => x.kind === 'sql' && find(x, 'step.sql_running')?.repairOf === v.stepId);
      const rr = rerun && find(rerun, 'step.sql_result');
      const rstate: Step['state'] = rr ? (rr.ok ? 'ok' : 'error') : finished ? (rerun ? 'cancelled' : 'ok') : 'running';
      return {
        ...base, kind: 'repair', label: rstate === 'running' ? 'Repairing the query' : 'Repaired the query', message: r.explanation, state: rstate,
        error: r.error.message, previousSql: sqlOf(all, r.failedStepId), sql: r.revisedSql, attempt: r.attempt, maxAttempts: r.maxAttempts,
      };
    }
    case 'check': {
      const cs = v.events.filter((e): e is Extract<StepEvent, { type: 'step.check' }> => e.type === 'step.check');
      const checks: CheckResult[] = cs.map((c) => ({ name: humanize(c.check), passed: c.verdict === 'pass', verdict: c.verdict, detail: c.message }));
      const label = cs.length === 1 ? (/^verif/i.test(cs[0].check) ? 'Verified the result' : `Checked ${checks[0].name.toLowerCase()}`) : `Ran ${cs.length} checks`;
      return { ...base, kind: 'check', label, state, checks };
    }
    case 'explore': {
      const e = find(v, 'step.explore');
      if (!e) return null;
      return { ...base, kind: 'explore', label: `${PROBE[e.probe] ?? humanize(e.probe)}${e.target ? ` · ${e.target}` : ''}`, state: 'ok', message: e.summary, durationMs: e.elapsedMs };
    }
    case 'candidates': {
      const c = find(v, 'step.candidates');
      if (!c) return null;
      const candidates: Candidate[] = c.candidates.map((x) => ({
        id: x.candidateId, label: x.label ?? x.candidateId, sql: x.sql, rowCount: x.rowCount, score: x.score, ok: x.ok,
        chosen: x.candidateId === c.chosenId,
        note: !x.ok ? 'failed' : x.agreesWith?.length ? `agrees with ${x.agreesWith.join(', ')}` : undefined,
      }));
      const method = humanize(c.method).toLowerCase();
      return { ...base, kind: 'candidates', label: `Compared ${candidates.length} candidates · ${method}`, state, candidates, message: c.rationale };
    }
    default:
      return null;
  }
}

function escalationStep(e: EscalationEvent, i: number): Step {
  return { id: `esc-step-${i}`, kind: 'escalation', label: `Handed off to ${modelName(e.to.model)}`, state: 'ok', message: e.reason, agent: modelName(e.from.model) };
}

/** Groups consecutive single-check steps into one "Ran N checks" row (less noise, like Claude). */
function mergeChecks(steps: Step[]): Step[] {
  const out: Step[] = [];
  for (const s of steps) {
    const prev = out[out.length - 1];
    if (s.kind === 'check' && prev?.kind === 'check' && s.checks && prev.checks) {
      const checks = [...prev.checks, ...s.checks];
      const worst = checks.some((c) => c.verdict === 'fail' || (!c.verdict && !c.passed)) ? 'error' : checks.some((c) => c.verdict === 'warn') ? 'warn' : 'ok';
      out[out.length - 1] = { ...prev, label: `Ran ${checks.length} checks`, checks, state: worst };
    } else out.push(s);
  }
  return out;
}

export function stepsOf(m: AnyView): Step[] {
  const finished = !m.streaming;
  const aborted = m.finishReason === 'aborted';
  const v1 = 'steps' in m ? m.steps : [];
  const steps: Step[] = [];
  let esc = 0;
  for (const v of v1) {
    // Escalations are rendered from m.escalations (they may share a stepId with a check).
    const s = v.kind === 'escalation' ? null : fromView({ ...v, events: v.events.filter((e) => e.type !== 'escalation') } as StepView, v1, finished, aborted);
    if (s) steps.push(s);
    for (const e of v.events) if (e.type === 'escalation') steps.push(escalationStep(e, esc++));
  }
  // Candidate runs fold into their comparison row once it arrives (one row, not N parallel queries).
  const cand = v1.filter((v) => v.kind === 'candidates');
  if (cand.length) {
    const runs = new Map(v1.filter((v) => v.kind === 'sql' && find(v, 'step.sql_running')?.purpose === 'candidate').map((v) => [v.stepId, v]));
    for (let i = steps.length - 1; i >= 0; i--) if (runs.has(steps[i].id)) steps.splice(i, 1);
    for (const s of steps) if (s.kind === 'candidates' && s.durationMs === undefined) {
      const ms = [...runs.values()].reduce((a, v) => a + (find(v, 'step.sql_result')?.elapsedMs ?? 0), 0);
      if (ms) s.durationMs = ms;
    }
  }
  const legacy = partsOf(m).filter((p): p is ToolCallPart => p.type === 'tool_call').map(toStep);
  const all = mergeChecks([...steps, ...legacy]);
  return finished ? all.map((s) => (s.state === 'running' ? { ...s, state: 'cancelled' as const } : s)) : all;
}

export const escalationsOf = (m: AnyView): EscalationEvent[] => ('escalations' in m ? m.escalations : []);

/** Failed steps that no later repair step fixed. */
export function unresolvedFailures(steps: Step[]): number {
  // A failure is resolved when a later repair or query succeeded (repair → re-run, or escalation → fresh query).
  const recovered = (i: number) => steps.slice(i + 1).some((r) => (r.kind === 'repair' || r.kind === 'sql') && (r.state === 'ok' || r.state === 'warn'));
  return steps.filter((s, i) => s.state === 'error' && s.kind !== 'escalation' && !recovered(i)).length;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function fmtMs(ms: number): string {
  // Edge cases: a bad or skewed clock (NaN, negative) reads 0ms; rounding never yields "60s" or "1m 60s".
  if (!Number.isFinite(ms) || ms < 0) ms = 0;
  if (ms < 999.5) return `${Math.round(ms)}ms`; // QA-A14: one format everywhere (7ms, 1.2s, 2m 5s)
  const s = ms / 1000;
  if (s < 9.95) return `${s.toFixed(1).replace(/\.0$/, '')}s`;
  const whole = Math.round(s);
  if (whole < 60) return `${whole}s`;
  if (whole < 3600) return `${Math.floor(whole / 60)}m ${whole % 60}s`;
  return `${Math.floor(whole / 3600)}h ${Math.floor((whole % 3600) / 60)}m`;
}

/** "Ran 3 queries, 1 repair, 2 checks · 12s" (Claude-style one-line summary). */
export function summarize(steps: Step[], elapsedMs?: number): string {
  const n = (k: StepKind) => steps.filter((s) => s.kind === k).length;
  const checks = steps.filter((s) => s.kind === 'check').reduce((a, s) => a + (s.checks?.length || 1), 0);
  const bits: string[] = [];
  // Schema/metadata reads (explore without SQL) aren't "queries"; a run with only those reads as exploring.
  const q = n('sql') + steps.filter((s) => s.kind === 'explore' && s.sql).length;
  if (q) bits.push(plural(q, 'query', 'queries'));
  if (n('repair')) bits.push(plural(n('repair'), 'repair'));
  if (checks) bits.push(plural(checks, 'check'));
  if (n('candidates')) bits.push('compared candidates');
  if (n('schema') + n('search')) bits.push(plural(n('schema') + n('search'), 'lookup'));
  if (n('chart')) bits.push(plural(n('chart'), 'chart'));
  if (n('other')) bits.push(plural(n('other'), 'other step'));
  let s = bits.length ? `Ran ${bits.join(', ')}` : n('explore') ? 'Explored the data' : `Used ${plural(steps.length, 'tool')}`;
  const stopped = steps.filter((x) => x.state === 'cancelled').length;
  if (stopped) s += `, ${stopped} stopped`;
  const ms = elapsedMs ?? steps.reduce((a, b) => a + (b.durationMs ?? 0), 0);
  if (ms > 0) s += ` · ${fmtMs(ms)}`;
  return s;
}

/** Footer cost: cents, like a receipt ("<$0.01" under a cent). The popover keeps the exact figure (fmtUsd). */
export function fmtUsdShort(x: number): string {
  if (!Number.isFinite(x) || x <= 0) return '$0';
  if (x < 0.01) return '<$0.01';
  return `$${x.toFixed(2)}`;
}

export function fmtUsd(x: number): string {
  if (!Number.isFinite(x) || x <= 0) return '$0';
  if (x < 0.01) return `$${x.toFixed(4)}`;
  return `$${x.toFixed(2)}`;
}

export function fmtTokens(n: number): string {
  if (!Number.isFinite(n) || n < 0) return '0';
  n = Math.round(n);
  if (n >= 999_500) return `${(n / 1e6).toFixed(n >= 9_950_000 ? 0 : 1).replace(/\.0$/, '')}M`;
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

/** v1 `Usage` (or legacy {queries, elapsedMs}) -> footer view. */
export function usageOf(m: AnyView): UsageView | undefined {
  const u = m.usage as (Usage & { model?: string }) | undefined;
  if (!u) return undefined;
  const l = u.latency;
  let timing: UsageView['timing'];
  if (l) {
    const parts = [
      { label: 'Queue', ms: l.queueMs ?? 0 },
      { label: 'Model', ms: l.modelMs ?? 0 },
      { label: 'SQL', ms: l.sqlMs ?? 0 },
    ].filter((p) => p.ms > 0);
    const other = l.totalMs - parts.reduce((a, p) => a + p.ms, 0);
    if (parts.length && other > 50) parts.push({ label: 'Other', ms: other });
    timing = parts.length ? parts : undefined;
  }
  const models = u.models?.length ? u.models : undefined;
  return {
    queries: u.queries, elapsedMs: l?.totalMs ?? u.elapsedMs,
    model: u.model ?? (models ? modelName(models[models.length - 1].model) : undefined),
    inputTokens: u.tokens?.input, outputTokens: u.tokens?.output, cacheRead: u.tokens?.cacheRead,
    costUsd: u.costUsd, costIsEstimate: u.costIsEstimate, firstTokenMs: l?.firstTokenMs, timing, models,
  };
}
