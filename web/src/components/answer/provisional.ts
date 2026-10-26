/**
 * Answer-surface view types (Team B).
 *
 * PROVISIONAL: these mirror the events Claude asked the contract to cover (tool steps, repair,
 * checks, candidates, abstain, clarifying question, escalation, cost/latency). They are read
 * defensively from the existing `Part` union plus optional fields, so the UI keeps working
 * whatever shape `web/src/api/contract.ts` (Team A) settles on; the adapters in `normalize.ts`
 * are the only place that needs to change when it lands.
 */
import type { AssistantView, ID, Part, ToolCallPart } from '../../lib/contract';

export type StepKind = 'sql' | 'repair' | 'check' | 'explore' | 'candidates' | 'chart' | 'schema' | 'search' | 'other';

export interface CheckResult { name: string; passed: boolean; detail?: string }
export interface Candidate { id: ID; label: string; sql?: string; rowCount?: number; score?: number; note?: string; chosen?: boolean }

/** A tool step as the timeline sees it. */
export interface Step {
  id: ID;
  kind: StepKind;
  label: string;
  /** 'cancelled' = still running when the turn ended (stopped or interrupted). */
  state: 'running' | 'ok' | 'error' | 'cancelled';
  sql?: string;
  rowCount?: number;
  durationMs?: number;
  message?: string;
  /** repair: the error that triggered it and the SQL it replaced. */
  error?: string;
  previousSql?: string;
  attempt?: number;
  checks?: CheckResult[];
  candidates?: Candidate[];
  agent?: string;
  resultId?: ID;
}

export interface AbstainPart { type: 'abstain'; id: ID; title?: string; reason: string; needed?: string[]; suggestions?: string[] }
export interface ClarifyPart { type: 'clarify'; id: ID; question: string; options: { id: ID; label: string; description?: string; value?: string }[]; allowFreeText?: boolean; answered?: string }
export interface EscalationPart { type: 'escalation'; id: ID; from: string; to: string; reason: string }

export interface UsageDetail {
  queries: number;
  elapsedMs: number;
  model?: string;
  inputTokens?: number;
  outputTokens?: number;
  costUsd?: number;
  repairs?: number;
  /** Phase breakdown, ms. */
  timing?: { label: string; ms: number }[];
}

export type AnyPart = Part | AbstainPart | ClarifyPart | EscalationPart;
export const isTool = (p: AnyPart): p is ToolCallPart => p.type === 'tool_call';
export const usageOf = (m: AssistantView) => m.usage as UsageDetail | undefined;
