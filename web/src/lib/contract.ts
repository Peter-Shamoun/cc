/**
 * CC response stream contract v1 (web/src/lib/contract.ts).
 * Follows docs/frontend-spec.md §5. Transport: POST /api/chat -> SSE, one JSON
 * StreamEvent per `data:` line. No PHI in URLs (§6.1).
 */
import type { PlotSpec } from '../contract/v1.gen';

export type ID = string; // opaque, non-PHI
export type ISODate = string;

/** Sensitivity of anything rendered. Server is authoritative. */
export type Sensitivity = 'none' | 'deidentified' | 'limited' | 'phi';

export interface PartBase {
  id: ID;
  parentId?: ID;
  sensitivity?: Sensitivity;
  createdAt?: ISODate;
}

export type ColumnType = 'string' | 'integer' | 'number' | 'currency' | 'percent' | 'date' | 'datetime' | 'boolean' | 'code';
export interface Column {
  key: string;
  label: string;
  type: ColumnType;
  format?: string;
  /** Masked unless the session allows PHI. */
  phi?: boolean;
  description?: string;
}

export interface TextPart extends PartBase { type: 'text'; text: string }
export interface ReasoningPart extends PartBase { type: 'reasoning'; summary: string; durationMs?: number }
export interface StatusPart extends PartBase { type: 'status'; label: string }

export interface ToolCallPart extends PartBase {
  type: 'tool_call';
  tool: 'run_sql' | 'describe_view' | 'list_views' | 'make_chart' | 'verify' | 'search_examples' | (string & {});
  label: string;
  state: 'running' | 'ok' | 'error';
  input?: { sql?: string; args?: Record<string, unknown> };
  output?: { rowCount?: number; columns?: Column[]; preview?: unknown[][]; message?: string };
  durationMs?: number;
  agent?: string;
}

export interface TablePart extends PartBase {
  type: 'table';
  title?: string;
  columns: Column[];
  /** Preview rows, row-major, aligned to columns. */
  rows: unknown[][];
  rowCount: number;
  resultId?: ID;
  csvFileId?: ID;
  sqlPartId?: ID;
  source?: { views: string[]; elapsedMs?: number; asOf?: ISODate };
  /** 1.11.0: set only in a SharedConversation shared without data. `rows` is empty, while `columns`/`rowCount` are real. */
  dataWithheld?: boolean;
}

export interface FilePart extends PartBase {
  type: 'file';
  fileId: ID;
  name: string;
  mime: 'text/csv' | 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet' | 'application/pdf' | 'image/png' | 'image/svg+xml' | 'application/zip' | (string & {});
  sizeBytes: number;
  rowCount?: number;
  previewable?: boolean;
  expiresAt?: ISODate;
}

export interface ImagePart extends PartBase {
  type: 'image';
  /** Fetched with auth through the adapter -> object URL. */
  blobId: ID;
  mime: 'image/png' | 'image/svg+xml' | 'image/webp';
  width: number;
  height: number;
  alt: string;
  caption?: string;
  svgBlobId?: ID;
}

export interface ChartPart extends PartBase {
  type: 'chart';
  library: 'vega-lite';
  /** Vega-Lite spec WITHOUT data.url. */
  spec: Record<string, unknown>;
  data?: { name: string; values: Record<string, unknown>[] };
  dataResultId?: ID;
  staticImageBlobId?: ID;
  title?: string;
  alt: string;
  /** 1.9.0: the table part this plot was drawn from (same message). */
  tablePartId?: ID;
  /** 1.18.0: the strict plot spec (source of truth; `spec`/`data` are derived). Validate with `parsePlot` before rendering. */
  plot?: PlotSpec;
}

export interface CodePart extends PartBase {
  type: 'code';
  language: 'sql' | 'python' | 'json' | 'text' | (string & {});
  code: string;
  title?: string;
  role?: 'query' | 'snippet' | 'spec';
  /** Default true for role=query. */
  collapsed?: boolean;
  rerunnable?: boolean;
}

export interface CalloutPart extends PartBase {
  type: 'callout';
  variant: 'assumption' | 'caveat' | 'data_quality' | 'phi_redaction' | 'info';
  title?: string;
  items: { id: ID; text: string; editable?: boolean }[];
}

export interface ArtifactPart extends PartBase {
  type: 'artifact';
  artifactId: ID;
  version: number;
  kind: 'table' | 'chart' | 'report' | 'code' | 'image';
  title: string;
  content: TablePart | ChartPart | CodePart | ImagePart | { markdown: string };
  autoOpen?: boolean;
}

export interface ErrorPart extends PartBase {
  type: 'error';
  code: 'query_failed' | 'access_denied' | 'timeout' | 'rate_limited' | 'auth_expired' | 'validation' | 'internal' | (string & {});
  title: string;
  message: string;
  details?: string;
  retryable: boolean;
  retryAfterMs?: number;
}

export type Part =
  | TextPart | ReasoningPart | StatusPart | ToolCallPart | TablePart | FilePart
  | ImagePart | ChartPart | CodePart | CalloutPart | ArtifactPart | ErrorPart;

export type StreamEvent =
  | { type: 'message.start'; messageId: ID; conversationId: ID; model?: string; strategy?: string }
  | { type: 'text.start'; partId: ID }
  | { type: 'text.delta'; partId: ID; delta: string }
  | { type: 'text.end'; partId: ID }
  | { type: 'reasoning.delta'; partId: ID; delta: string }
  | { type: 'reasoning.end'; partId: ID; durationMs: number }
  | { type: 'code.delta'; partId: ID; delta: string }
  | { type: 'part'; part: Part }
  | { type: 'part.patch'; partId: ID; patch: Partial<Part> }
  | { type: 'status'; label: string | null }
  | { type: 'message.end'; messageId: ID; finishReason: 'stop' | 'error' | 'aborted'; usage?: { queries: number; elapsedMs: number } }
  | { type: 'heartbeat' };

/** Persisted message shapes (what transcript storage saves). */
export interface AssistantMessage {
  id: ID; role: 'assistant'; parts: Part[]; createdAt: ISODate; parentId?: ID; feedback?: 'up' | 'down';
}
export interface UserMessage {
  id: ID; role: 'user'; text: string; attachments?: FilePart[]; createdAt: ISODate; parentId?: ID;
  /** Contract 1.2: the user message this one edits (its sibling; editing the first question makes a new root). */
  editOf?: ID;
}
export type Message = UserMessage | AssistantMessage;

/** Client-side view state for an assistant message while it streams (memory only). */
export interface AssistantView extends AssistantMessage {
  streaming: boolean;
  status: string | null;
  /** Part ids whose text is still streaming (for the caret). */
  openText: ID[];
  finishReason?: 'stop' | 'error' | 'aborted';
  usage?: { queries: number; elapsedMs: number };
}

export const KNOWN_PART_TYPES = ['text', 'reasoning', 'status', 'tool_call', 'table', 'file', 'image', 'chart', 'code', 'callout', 'artifact', 'error'] as const;

export function newAssistantView(id: ID): AssistantView {
  return { id, role: 'assistant', parts: [], createdAt: new Date().toISOString(), streaming: true, status: null, openText: [] };
}

function upsert(parts: Part[], part: Part): Part[] {
  const i = parts.findIndex((p) => p.id === part.id);
  if (i === -1) return [...parts, part];
  const next = parts.slice();
  next[i] = part;
  return next;
}

function appendTo(parts: Part[], partId: ID, delta: string, make: () => Part, field: 'text' | 'summary' | 'code'): Part[] {
  const i = parts.findIndex((p) => p.id === partId);
  if (i === -1) {
    const p = make() as unknown as Record<string, string>;
    p[field] = delta;
    return [...parts, p as unknown as Part];
  }
  const cur = parts[i] as unknown as Record<string, string>;
  const next = parts.slice();
  next[i] = { ...cur, [field]: (cur[field] ?? '') + delta } as unknown as Part;
  return next;
}

/**
 * Pure reducer (§5 rules): part order = first appearance; `part` with an
 * existing id replaces it; unknown part types are kept and rendered as an
 * "Unsupported content" chip by the UI.
 */
export function reduce(m: AssistantView, ev: StreamEvent): AssistantView {
  switch (ev.type) {
    case 'message.start':
      return { ...m, id: ev.messageId, streaming: true };
    case 'text.start':
      return { ...m, status: null, parts: upsert(m.parts, { type: 'text', id: ev.partId, text: '' }), openText: [...m.openText, ev.partId] };
    case 'text.delta': {
      const openText = m.openText.includes(ev.partId) ? m.openText : [...m.openText, ev.partId];
      return { ...m, openText, parts: appendTo(m.parts, ev.partId, ev.delta, () => ({ type: 'text', id: ev.partId, text: '' }), 'text') };
    }
    case 'text.end':
      return { ...m, openText: m.openText.filter((id) => id !== ev.partId) };
    case 'reasoning.delta':
      return { ...m, parts: appendTo(m.parts, ev.partId, ev.delta, () => ({ type: 'reasoning', id: ev.partId, summary: '' }), 'summary') };
    case 'reasoning.end':
      return { ...m, parts: m.parts.map((p) => (p.id === ev.partId && p.type === 'reasoning' ? { ...p, durationMs: ev.durationMs } : p)) };
    case 'code.delta':
      return { ...m, parts: appendTo(m.parts, ev.partId, ev.delta, () => ({ type: 'code', id: ev.partId, language: 'sql', code: '' }), 'code') };
    case 'part':
      return { ...m, parts: upsert(m.parts, ev.part) };
    case 'part.patch':
      return { ...m, parts: m.parts.map((p) => (p.id === ev.partId ? ({ ...p, ...ev.patch, id: p.id, type: p.type } as Part) : p)) };
    case 'status':
      return { ...m, status: ev.label };
    case 'message.end':
      return { ...m, streaming: false, status: null, openText: [], finishReason: ev.finishReason, usage: ev.usage };
    default:
      return m;
  }
}

export function isArtifactable(p: Part): p is TablePart | ChartPart | CodePart | ImagePart {
  return p.type === 'table' || p.type === 'chart' || p.type === 'code' || p.type === 'image';
}
