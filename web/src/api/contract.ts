/**
 * CC chat stream contract v1 — TypeScript mirror of api/contract/v1.schema.json.
 * Prose spec: api/CONTRACT.md. If you need a new event, change CONTRACT.md + the schema + this
 * file FIRST (minor version bump), then make the backend emit it.
 *
 * Drift gate: these types are pinned to the schema-generated ../contract/v1.gen.ts by
 * ../contract/compat.test.ts, so `tsc -b` fails if this file and the schema disagree.
 *
 * Part shapes predating v1 live in ../lib/contract and are re-exported here unchanged.
 */
import type {
  AssistantView as LegacyAssistantView, CalloutPart, ChartPart, CodePart, Column, ColumnType, ErrorPart,
  FilePart, ID, ImagePart, ISODate, Part as LegacyPart, ReasoningPart, Sensitivity, StatusPart, TablePart,
  TextPart, ToolCallPart, ArtifactPart, PartBase, StreamEvent as LegacyStreamEvent,
} from '../lib/contract';
import { reduce as legacyReduce } from '../lib/contract';
import type { Source } from '../contract/v1.gen';

export type {
  CalloutPart, ChartPart, CodePart, Column, ColumnType, ErrorPart, FilePart, ID, ImagePart, ISODate, ReasoningPart,
  Sensitivity, StatusPart, TablePart, TextPart, ToolCallPart, ArtifactPart, PartBase,
};

export const CONTRACT_VERSION = '1.10.0';
export const CONTRACT_MAJOR = 1;

// ---------------------------------------------------------------- new parts (v1)

export type AbstainCategory = 'out_of_scope' | 'phi_request' | 'data_unavailable' | 'ambiguous' | 'low_confidence' | 'unsupported' | 'policy';
/** "Can't answer" is an outcome, not an error: render as a calm card with clickable suggestions. */
export interface AbstainPart extends PartBase {
  type: 'abstain';
  category: AbstainCategory;
  /** User-facing markdown. */
  reason: string;
  details?: string;
  /** Rephrasings the user can click to send as a new question. */
  suggestions: { id: ID; text: string }[];
}

/** The analyzer needs input. Reply via ChatRequest.resume (see CONTRACT.md §6). */
export interface ClarificationPart extends PartBase {
  type: 'clarification';
  clarificationId: ID;
  question: string;
  choices?: { id: ID; label: string; description?: string }[];
  allowFreeText: boolean;
  multiSelect?: boolean;
  expiresAt?: ISODate;
  /** Set by the server on reload once the user replied. */
  answered?: boolean;
}

export type Part = LegacyPart | AbstainPart | ClarificationPart;

// ---------------------------------------------------------------- shared shapes

export interface ColumnSummary { name: string; type?: ColumnType }
export interface StepError { code: string; message: string }
export interface ModelRef { model: string; tier?: 'small' | 'medium' | 'large'; provider?: string }

/** 1.6.0 (CONTRACT.md §7): where the run's wall time went. Unknown extra stages may appear; ignore them. */
export interface LatencyStages { plan: number; sql: number; repair: number; checks: number; render: number }

export interface Usage {
  queries: number;
  elapsedMs: number;
  tokens?: { input: number; output: number; cacheRead?: number; cacheWrite?: number };
  costUsd?: number;
  /** true when costUsd is computed from a price table rather than billed. Demo mode: always true. */
  costIsEstimate?: boolean;
  latency?: {
    firstEventMs?: number; firstStepMs?: number; firstTokenMs?: number; modelMs?: number; sqlMs?: number; queueMs?: number; totalMs: number;
    /** 1.6.0: wall-clock ms per stage; they sum to about totalMs. */
    stages?: LatencyStages;
  };
  models?: { model: string; role?: string; inputTokens: number; outputTokens: number; costUsd?: number; calls?: number }[];
}

/** 1.10.0: `regenerate.style` (CONTRACT.md §8.1). */
export type RegenerateStyle = 'default' | 'concise' | 'detailed';

export type FinishReason = 'stop' | 'abstain' | 'needs_input' | 'error' | 'aborted';

// ---------------------------------------------------------------- events

interface Seq { seq: number }

export interface MessageStartEvent extends Seq {
  type: 'message.start'; messageId: ID; conversationId: ID; contractVersion: string;
  model?: string; strategy?: string; parentId?: ID;
  resumeOf?: { clarificationId: ID; messageId: ID };
  regenerateOf?: ID;
  /** 1.10.0: echoes `regenerate.style` when it is not `default`. */
  regenerateStyle?: RegenerateStyle;
  /** Set when this answer is for an edited question (ChatMessage.editOf). */
  editOf?: ID;
}
export interface StatusEvent extends Seq { type: 'status'; label: string | null }

export type SqlPurpose = 'explore' | 'answer' | 'candidate' | 'repair' | 'check';
export interface StepSqlRunningEvent extends Seq {
  type: 'step.sql_running'; stepId: ID; sql: string; purpose: SqlPurpose; attempt: number;
  candidateId?: ID; repairOf?: ID; label?: string;
}
/** Never carries row values — only counts and column names/types. */
export interface StepSqlResultEvent extends Seq {
  type: 'step.sql_result'; stepId: ID; ok: boolean; elapsedMs: number;
  rowCount?: number; columns?: ColumnSummary[]; truncated?: boolean; error?: StepError;
}
export interface StepRepairAttemptEvent extends Seq {
  type: 'step.repair_attempt'; stepId: ID; failedStepId: ID; attempt: number; maxAttempts?: number;
  error: StepError; revisedSql: string; explanation?: string;
}
export type CheckVerdict = 'pass' | 'warn' | 'fail';
export interface StepCheckEvent extends Seq {
  type: 'step.check'; stepId: ID; check: string; verdict: CheckVerdict; message: string;
  targetStepId?: ID; action?: 'none' | 'repair' | 'caveat' | 'abstain' | 'escalate' | 'clarify';
}
export interface StepExploreEvent extends Seq {
  type: 'step.explore'; stepId: ID; probe: 'list_views' | 'describe_view' | 'sample_values' | 'lookup_glossary' | 'lookup_dictionary' | 'search_examples' | 'profile' | (string & {});
  target?: string; summary: string; elapsedMs?: number;
}
export interface Candidate { candidateId: ID; label?: string; sql: string; ok: boolean; rowCount?: number; score?: number; agreesWith?: ID[]; stepId?: ID }
export interface StepCandidatesEvent extends Seq {
  type: 'step.candidates'; stepId: ID; candidates: Candidate[]; chosenId: ID | null; method: string; rationale?: string;
}
export interface EscalationEvent extends Seq {
  type: 'escalation'; from: ModelRef; to: ModelRef; reason: string;
  trigger: 'verifier_fail' | 'low_confidence' | 'repair_exhausted' | 'complexity' | 'disagreement' | 'user';
  stepId?: ID;
}
export interface TextStartEvent extends Seq { type: 'text.start'; partId: ID }
export interface TextDeltaEvent extends Seq { type: 'text.delta'; partId: ID; delta: string }
export interface TextEndEvent extends Seq { type: 'text.end'; partId: ID }
export interface ReasoningDeltaEvent extends Seq { type: 'reasoning.delta'; partId: ID; delta: string }
export interface ReasoningEndEvent extends Seq { type: 'reasoning.end'; partId: ID; durationMs: number }
export interface CodeDeltaEvent extends Seq { type: 'code.delta'; partId: ID; delta: string }
/** 1.9.0 (§5.1): a table starts streaming; rows follow in table.rows, then the complete `part` (same id) replaces it. */
export interface TableStartEvent extends Seq {
  type: 'table.start'; partId: ID; columns: Column[]; title?: string; sensitivity?: Sensitivity; stepId?: ID; sqlPartId?: ID;
}
/** 1.9.0: preview rows; `offset` = index of the first row, so a replayed chunk overwrites instead of duplicating. */
export interface TableRowsEvent extends Seq { type: 'table.rows'; partId: ID; offset: number; rows: unknown[][] }
export interface PartEvent extends Seq { type: 'part'; part: Part }
export interface PartPatchEvent extends Seq { type: 'part.patch'; partId: ID; patch: Record<string, unknown> }
export interface MessageEndEvent extends Seq {
  type: 'message.end'; messageId: ID; finishReason: FinishReason; usage: Usage;
  /** 1.3.0: PHI-free title the server derived and already stored (first answer, generic title only). */
  conversationTitle?: string;
  /** 1.8.0: citations, answer sources first (CONTRACT.md §14). Helpers: src/contract/sources.ts. */
  sources?: Source[];
}
export interface HeartbeatEvent { type: 'heartbeat'; at?: ISODate }

export type StepEvent = StepSqlRunningEvent | StepSqlResultEvent | StepRepairAttemptEvent | StepCheckEvent | StepExploreEvent | StepCandidatesEvent;

export type StreamEvent =
  | MessageStartEvent | StatusEvent | StepEvent | EscalationEvent
  | TextStartEvent | TextDeltaEvent | TextEndEvent | ReasoningDeltaEvent | ReasoningEndEvent | CodeDeltaEvent
  | TableStartEvent | TableRowsEvent | PartEvent | PartPatchEvent | MessageEndEvent | HeartbeatEvent;

export const EVENT_TYPES = [
  'message.start', 'status', 'step.sql_running', 'step.sql_result', 'step.repair_attempt', 'step.check', 'step.explore',
  'step.candidates', 'escalation', 'text.start', 'text.delta', 'text.end', 'reasoning.delta', 'reasoning.end',
  'code.delta', 'table.start', 'table.rows', 'part', 'part.patch', 'message.end', 'heartbeat',
] as const satisfies readonly StreamEvent['type'][];

export const PART_TYPES = [
  'text', 'reasoning', 'status', 'tool_call', 'table', 'file', 'image', 'chart', 'code', 'callout', 'artifact', 'error',
  'abstain', 'clarification',
] as const satisfies readonly Part['type'][];

const STEP_TYPES = new Set<string>(['step.sql_running', 'step.sql_result', 'step.repair_attempt', 'step.check', 'step.explore', 'step.candidates']);
export function isStepEvent(ev: { type: string }): ev is StepEvent {
  return STEP_TYPES.has(ev.type);
}

// ---------------------------------------------------------------- capabilities (1.4.0)

/** GET /api/capabilities: strategies + models for a picker. Generated from the schema (single source of truth). */
export type { Capabilities, StrategyOption, ModelOption, CostTier } from '../contract/v1.gen';
/** 1.7.0: answer feedback (CONTRACT.md §13). Client: src/contract/feedback.ts. */
export type { Feedback, FeedbackRating, FeedbackReason, FeedbackRequest } from '../contract/v1.gen';
/** 1.8.0: citations (§14) and conversation export (§15). */
export type { Source, ConversationExport } from '../contract/v1.gen';

// ---------------------------------------------------------------- requests

export interface ChatRequest {
  conversationId?: ID;
  /** `editOf`: id of an earlier user message this one replaces (its sibling; editing the first question makes a new root). */
  messages: { id?: ID; role: 'user'; text: string; parentId?: ID; editOf?: ID }[];
  /** 1.4.0: `strategy` / `model` are ids from GET /api/capabilities (see `Capabilities`). */
  options?: { strategy?: string; model?: string; [k: string]: unknown };
  /** Answer a ClarificationPart. `messages` carries the user's reply as a normal user turn. */
  resume?: { clarificationId: ID; choiceIds?: ID[]; text?: string };
  /**
   * Re-run the assistant message `messageId` for the same user turn. The new message is a sibling branch, i.e. a new
   * version of the answer. 1.10.0: `style` restyles it ('More concise' / 'More detailed'; CONTRACT.md §8.1).
   */
  regenerate?: { messageId: ID; style?: RegenerateStyle };
  /** Demo backend only: force a scripted scenario (see CONTRACT.md §9). Ignored in sim/prod. */
  scenario?: 'table' | 'chart' | 'repair' | 'abstain' | 'clarify' | 'escalation' | 'error' | 'candidates' | 'slow';
}

/** `code` known values: see the schema. 1.5.0: `payload_too_large` (413) with `legacyCode: 'validation'` and `maxBytes`. */
export interface HttpError { error: { code: string; message: string; legacyCode?: string; maxBytes?: number } }

// ---------------------------------------------------------------- client view + reducer

/** One row in the "work" timeline: a step grouped from its step.* events. */
export interface StepView {
  stepId: ID;
  kind: 'sql' | 'repair' | 'check' | 'explore' | 'candidates' | 'escalation';
  state: 'running' | 'ok' | 'error' | 'warn';
  events: (StepEvent | EscalationEvent)[];
}

export interface AssistantViewV1 extends Omit<LegacyAssistantView, 'finishReason' | 'usage' | 'parts'> {
  parts: Part[];
  steps: StepView[];
  escalations: EscalationEvent[];
  finishReason?: FinishReason;
  usage?: Usage;
  contractVersion?: string;
  lastSeq: number;
  /** 1.9.0: ids of table parts still streaming (table.start seen, complete `part` not yet). Show a "loading rows" hint. */
  streamingTables?: ID[];
  /** 1.8.0: message.end.sources. */
  sources?: Source[];
}

export function newAssistantViewV1(id: ID): AssistantViewV1 {
  return { id, role: 'assistant', parts: [], createdAt: new Date().toISOString(), streaming: true, status: null, openText: [], steps: [], escalations: [], lastSeq: -1 };
}

function stepKind(ev: StepEvent | EscalationEvent): StepView['kind'] {
  switch (ev.type) {
    case 'step.sql_running': case 'step.sql_result': return 'sql';
    case 'step.repair_attempt': return 'repair';
    case 'step.check': return 'check';
    case 'step.explore': return 'explore';
    case 'step.candidates': return 'candidates';
    default: return 'escalation';
  }
}

function stepState(ev: StepEvent | EscalationEvent, prev?: StepView['state']): StepView['state'] {
  switch (ev.type) {
    case 'step.sql_running': return 'running';
    case 'step.sql_result': return ev.ok ? 'ok' : 'error';
    case 'step.check': return ev.verdict === 'pass' ? 'ok' : ev.verdict === 'warn' ? 'warn' : 'error';
    case 'step.candidates': return ev.chosenId ? 'ok' : 'warn';
    default: return prev === 'running' ? 'running' : 'ok';
  }
}

function upsertStep(steps: StepView[], stepId: ID, ev: StepEvent | EscalationEvent): StepView[] {
  const i = steps.findIndex((s) => s.stepId === stepId);
  if (i === -1) return [...steps, { stepId, kind: stepKind(ev), state: stepState(ev), events: [ev] }];
  const cur = steps[i];
  const next = steps.slice();
  next[i] = { ...cur, state: stepState(ev, cur.state), events: [...cur.events, ev] };
  return next;
}

/**
 * Pure reducer for v1 streams. Duplicate/old seq numbers (e.g. after a Last-Event-ID resume) are dropped.
 * Unknown event types are ignored (forward compatibility, CONTRACT.md §3).
 */
export function reduceV1(m: AssistantViewV1, ev: StreamEvent | { type: string; seq?: number }): AssistantViewV1 {
  const e = ev as StreamEvent;
  if (e.type === 'heartbeat') return m;
  const seq = (ev as { seq?: number }).seq;
  if (typeof seq === 'number') {
    if (seq <= m.lastSeq) return m;
    m = { ...m, lastSeq: seq };
  }
  if (isStepEvent(e)) return { ...m, status: m.status, steps: upsertStep(m.steps, e.stepId, e) };
  switch (e.type) {
    case 'escalation': {
      const sid = e.stepId ?? `esc-${m.escalations.length}`;
      return { ...m, escalations: [...m.escalations, e], steps: upsertStep(m.steps, sid, e) };
    }
    case 'message.start':
      return { ...m, id: e.messageId, streaming: true, contractVersion: e.contractVersion };
    case 'message.end': {
      const steps = m.steps.map((s) => (s.state === 'running' ? { ...s, state: e.finishReason === 'aborted' ? 'error' as const : 'ok' as const } : s));
      // a table whose complete part never came is partial: drop it (1.9.0, CONTRACT.md §5.1)
      const partial = new Set(m.streamingTables ?? []);
      const parts = partial.size ? m.parts.filter((p) => !partial.has(p.id)) : m.parts;
      return { ...m, steps, parts, streamingTables: [], streaming: false, status: null, openText: [], finishReason: e.finishReason, usage: e.usage, sources: e.sources };
    }
    case 'table.start': {
      if (m.parts.some((p) => p.id === e.partId)) return m;
      const table = { type: 'table', id: e.partId, columns: e.columns, rows: [], rowCount: 0, title: e.title, sensitivity: e.sensitivity, stepId: e.stepId, sqlPartId: e.sqlPartId } as unknown as Part;
      return { ...m, parts: [...m.parts, table], streamingTables: [...(m.streamingTables ?? []), e.partId] };
    }
    case 'table.rows': {
      if (!(m.streamingTables ?? []).includes(e.partId)) return m;
      return { ...m, parts: m.parts.map((p) => {
        if (p.id !== e.partId || p.type !== 'table') return p;
        const rows = p.rows.slice(0, e.offset);
        rows.push(...e.rows);
        return { ...p, rows, rowCount: Math.max(p.rowCount, rows.length) };
      }) };
    }
    case 'part': {
      const next = { ...m, streamingTables: (m.streamingTables ?? []).filter((id) => id !== e.part.id) };
      const legacy = legacyReduce(next as unknown as LegacyAssistantView, e as unknown as LegacyStreamEvent);
      return { ...next, parts: legacy.parts as Part[], status: legacy.status, openText: legacy.openText };
    }
    case 'part.patch': case 'text.start': case 'text.delta': case 'text.end':
    case 'reasoning.delta': case 'reasoning.end': case 'code.delta': case 'status': {
      const legacy = legacyReduce(m as unknown as LegacyAssistantView, e as unknown as LegacyStreamEvent);
      return { ...m, parts: legacy.parts as Part[], status: legacy.status, openText: legacy.openText };
    }
    default:
      return m;
  }
}
