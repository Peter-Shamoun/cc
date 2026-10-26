import type { AssistantView, ToolCallPart } from '../../lib/contract';
import type { AnyPart, CheckResult, Candidate, Step, StepKind, UsageDetail } from './provisional';

const KIND: Record<string, StepKind> = {
  run_sql: 'sql', sql: 'sql', query: 'sql',
  repair: 'repair', repair_sql: 'repair', fix_sql: 'repair',
  verify: 'check', verifier: 'check', check: 'check', checks: 'check',
  explore: 'explore', probe: 'explore', explore_probe: 'explore',
  candidates: 'candidates', compare_candidates: 'candidates', select_candidate: 'candidates',
  make_chart: 'chart', describe_view: 'schema', list_views: 'schema', search_examples: 'search',
};

type Loose = ToolCallPart & { kind?: string; error?: string; previousSql?: string; attempt?: number; checks?: CheckResult[]; candidates?: Candidate[]; output?: ToolCallPart['output'] & { resultId?: string; checks?: CheckResult[]; candidates?: Candidate[] } };

export function toStep(p: ToolCallPart): Step {
  const l = p as Loose;
  const args = (p.input?.args ?? {}) as Record<string, unknown>;
  return {
    id: p.id,
    kind: KIND[l.kind ?? p.tool] ?? 'other',
    label: p.label,
    state: p.state,
    sql: p.input?.sql,
    rowCount: p.output?.rowCount,
    durationMs: p.durationMs,
    message: p.output?.message,
    error: l.error ?? (args.error as string | undefined),
    previousSql: l.previousSql ?? (args.previousSql as string | undefined),
    attempt: l.attempt ?? (args.attempt as number | undefined),
    checks: l.checks ?? l.output?.checks ?? (args.checks as CheckResult[] | undefined),
    candidates: l.candidates ?? l.output?.candidates ?? (args.candidates as Candidate[] | undefined),
    agent: p.agent,
    resultId: l.output?.resultId,
  };
}

export function stepsOf(m: AssistantView): Step[] {
  const steps = (m.parts as AnyPart[]).filter((p): p is ToolCallPart => p.type === 'tool_call').map(toStep);
  return m.streaming ? steps : steps.map((s) => (s.state === 'running' ? { ...s, state: 'cancelled' as const } : s));
}

/** Failed steps that no later repair step fixed. */
export function unresolvedFailures(steps: Step[]): number {
  return steps.filter((s, i) => s.state === 'error' && s.kind !== 'repair' && !steps.slice(i + 1).some((r) => r.kind === 'repair' && r.state === 'ok')).length;
}

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

export function fmtMs(ms: number): string {
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const s = ms / 1000;
  if (s < 60) return `${s < 10 ? s.toFixed(1) : Math.round(s)}s`;
  return `${Math.floor(s / 60)}m ${Math.round(s % 60)}s`;
}

/** "Ran 3 queries, 1 repair, 2 checks · 12s" (Claude-style one-line summary). */
export function summarize(steps: Step[], elapsedMs?: number): string {
  const n = (k: StepKind) => steps.filter((s) => s.kind === k).length;
  const bits: string[] = [];
  const q = n('sql') + n('explore');
  if (q) bits.push(plural(q, 'query', 'queries'));
  if (n('repair')) bits.push(plural(n('repair'), 'repair'));
  if (n('check')) bits.push(plural(n('check'), 'check'));
  if (n('candidates')) bits.push('compared candidates');
  if (n('schema') + n('search')) bits.push(plural(n('schema') + n('search'), 'lookup'));
  if (n('chart')) bits.push(plural(n('chart'), 'chart'));
  if (n('other')) bits.push(plural(n('other'), 'other step'));
  let s = bits.length ? `Ran ${bits.join(', ')}` : `Used ${plural(steps.length, 'tool')}`;
  const stopped = steps.filter((x) => x.state === 'cancelled').length;
  if (stopped) s += `, ${stopped} stopped`;
  const ms = elapsedMs ?? steps.reduce((a, b) => a + (b.durationMs ?? 0), 0);
  if (ms > 0) s += ` · ${fmtMs(ms)}`;
  return s;
}

export function fmtUsd(x: number): string {
  if (x === 0) return '$0';
  if (x < 0.01) return `$${x.toFixed(4)}`;
  return `$${x.toFixed(2)}`;
}

export function fmtTokens(n: number): string {
  return n >= 1000 ? `${(n / 1000).toFixed(n >= 10000 ? 0 : 1)}k` : String(n);
}

export const usage = (m: AssistantView) => m.usage as UsageDetail | undefined;
