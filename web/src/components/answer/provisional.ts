/**
 * Answer-surface view model (Team B).
 *
 * The UI renders these flat view types; `normalize.ts` is the only place that reads the wire
 * contract (web/src/api/contract.ts, v1) and the legacy `tool_call` parts, and maps both here.
 */
import type { ID } from '../../lib/contract';
import type { Usage } from '../../api/contract';
export type { AbstainPart, ClarificationPart, EscalationEvent, Usage } from '../../api/contract';

export type StepKind = 'sql' | 'repair' | 'check' | 'explore' | 'candidates' | 'escalation' | 'chart' | 'schema' | 'search' | 'other';

export type Verdict = 'pass' | 'warn' | 'fail';
export interface CheckResult { name: string; passed: boolean; verdict?: Verdict; detail?: string }
export interface Candidate { id: ID; label: string; sql?: string; maskedLiterals?: number; rowCount?: number; score?: number; note?: string; chosen?: boolean; ok?: boolean }

/** A step as the timeline sees it. */
export interface Step {
  id: ID;
  kind: StepKind;
  label: string;
  /** 'cancelled' = still running when the turn ended (stopped or interrupted). */
  state: 'running' | 'ok' | 'error' | 'warn' | 'cancelled';
  sql?: string;
  /** 1.14.0: literals in the shown `sql` replaced by `?` (CONTRACT.md §10). */
  maskedLiterals?: number;
  rowCount?: number;
  truncated?: boolean;
  columns?: string[];
  durationMs?: number;
  message?: string;
  /** repair: the error that triggered it and the SQL it replaced. */
  error?: string;
  previousSql?: string;
  attempt?: number;
  maxAttempts?: number;
  checks?: CheckResult[];
  candidates?: Candidate[];
  agent?: string;
  resultId?: ID;
}

/** Flattened usage for the footer (from v1 `Usage`, or the legacy {queries, elapsedMs}). */
export interface UsageView {
  queries: number;
  elapsedMs: number;
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
  cacheRead?: number;
  costUsd?: number;
  costIsEstimate?: boolean;
  firstTokenMs?: number;
  /** Phase breakdown, ms. */
  timing?: { label: string; ms: number }[];
  models?: NonNullable<Usage['models']>;
}
