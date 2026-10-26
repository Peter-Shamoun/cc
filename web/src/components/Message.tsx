import { memo, useEffect, useId, useRef, useState, type ReactNode } from 'react';
import { ChevronDown, ChevronLeft, ChevronRight, Code2, FileText, Minimize2, MoreHorizontal, Pencil, Plus, Printer, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-react';
import clsx from 'clsx';
import type { AssistantView, Part, TablePart, UserMessage } from '../lib/contract';
import { KNOWN_PART_TYPES } from '../lib/contract';
import { Markdown } from './parts/Markdown';
import { CodePartView } from './parts/CodeBlock';
import { DataTable } from './parts/DataTable';
import { ChartView } from './parts/ChartView';
import { ArtifactCard, Callout, ErrorView, FileCard, ImageView, ReasoningView, Unsupported } from './parts/misc';
import { CopyButton, IconButton } from './ui';
import { StepTimeline } from './answer/StepTimeline';
import { queriesOf, queryForTable, SqlInspector, SqlWithheldNote, TableSql } from './answer/SqlInspector';
import { AbstainCard, ClarifyCard, EscalationNote, StoppedNote, TableSkeleton } from './answer/States';
import { AnswerMeta } from './answer/AnswerMeta';
import { SourcesChip } from './answer/Sources';
import { escalationsOf, partsOf, stepsOf, usageOf, type AnyView } from './answer/normalize';
import type { OnReply } from './answer/States';
import type { RegenerateStyle, Source } from '../api/contract';
import { copyText, tableToTsv, visible } from '../lib/csv';
import { MAX_FEEDBACK_COMMENT } from '../contract/feedback';
import { feedbackFor, feedbackReasons, recordFeedback, sqlForClipboard, storedVote, type Vote } from '../lib/feedback';
import { Menu, useMenu, useToast, type MenuItem } from '../shell/primitives';
import { isTransportStatus, transportAnnouncement } from '../lib/useChat';

export type { Artifact } from './artifacts';
import { toArtifact, type Artifact } from './artifacts';

/** Markdown export for Copy (§4.12): text, tables as GFM (PHI columns dropped), SQL, callouts. */
export function messageMarkdown(m: AnyView): string {
  return partsOf(m)
    .map((p) => {
      switch (p.type) {
        case 'abstain': return [p.reason, p.details, p.suggestions.length ? `Try asking:\n${p.suggestions.map((s) => `- ${s.text}`).join('\n')}` : ''].filter(Boolean).join('\n\n');
        case 'clarification': return [p.question, ...(p.choices ?? []).map((c) => `- ${c.label}`)].join('\n');
        case 'text': return p.text;
        case 'code': return `\`\`\`${p.language}\n${p.code}\n\`\`\``;
        case 'callout': return `**${p.title ?? p.variant}**\n${p.items.map((i) => `- ${i.text}`).join('\n')}`;
        case 'table': {
          const cols = p.columns.map((c, i) => [c, i] as const).filter(([c]) => visible(c));
          return [`| ${cols.map(([c]) => c.label).join(' | ')} |`, `|${cols.map(() => '---').join('|')}|`, ...p.rows.map((r) => `| ${cols.map(([, i]) => String(r[i] ?? '')).join(' | ')} |`), ...(p.dataWithheld ? [`\n_Data not shared (${p.rowCount} rows)_`] : [])].join('\n');
        }
        default: return '';
      }
    })
    .filter(Boolean)
    .join('\n\n');
}

/** Long pastes collapse to ~9 lines with a fade and "Show more" (ChatGPT/Claude). */
const LONG_CHARS = 700;
const LONG_LINES = 9;
export function isLongQuestion(text: string): boolean {
  return text.length > LONG_CHARS || text.split('\n').length > LONG_LINES;
}
function UserText({ text }: { text: string }) {
  const long = isLongQuestion(text);
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="max-w-[70%] rounded-[18px] bg-muted px-4 py-2.5 leading-normal">
      <div id={id} className={clsx('whitespace-pre-wrap break-words', long && !open && 'max-h-[13.5em] overflow-hidden [mask-image:linear-gradient(to_bottom,black_70%,transparent)]')} data-testid="user-text">{text}</div>
      {long && (
        <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)}
          className="mt-1 font-sans text-sm font-medium text-fg-muted underline-offset-2 hover:text-fg hover:underline" data-testid="user-show-more">
          {open ? 'Show less' : 'Show more'}
        </button>
      )}
    </div>
  );
}

/**
 * QA-A21: editing and version switches unmount the focused control (the edit box closes; a switched version
 * is a different message, so its row remounts). Put focus back on the matching control in the row that now
 * sits at the same position, or on the composer, instead of letting it fall to <body>.
 */
export function refocusInRow(from: Element | null, pick: (row: HTMLElement) => HTMLElement | null | undefined, tries = 6) {
  const row = from?.closest<HTMLElement>('[data-message-id]') ?? null;
  const rows = () => Array.from(document.querySelectorAll<HTMLElement>('[data-message-id]'));
  const idx = row ? rows().indexOf(row) : -1;
  const lost = () => !document.activeElement || document.activeElement === document.body || !document.activeElement.isConnected;
  const run = (tries: number) => requestAnimationFrame(() => {
    if (!lost()) return;
    const now = idx >= 0 ? rows()[idx] : undefined;
    const target = now ? pick(now) : null;
    if (target) target.focus({ preventScroll: true });
    else if (tries > 0) run(tries - 1);
    else document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]')?.focus({ preventScroll: true });
  });
  run(tries);
}
const enabled = (el: HTMLButtonElement | null | undefined) => (el && !el.disabled ? el : null);

export const UserBubble = memo(function UserBubble({ msg, onEdit, version, onVersion, disabled }: { msg: UserMessage; onEdit?: (t: string) => void; version?: { current: number; total: number }; onVersion?: (k: number) => void; disabled?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(msg.text);
  const actionsRef = useRef<HTMLDivElement>(null);
  const rowRef = useRef<HTMLDivElement>(null);
  const backToEdit = useRef(false);
  // QA-A21: closing the edit box (Esc, Cancel, or an unchanged Send) returns focus to "Edit message".
  useEffect(() => {
    if (editing || !backToEdit.current) return;
    backToEdit.current = false;
    enabled(actionsRef.current?.querySelector<HTMLButtonElement>('button[aria-label="Edit message"]'))?.focus({ preventScroll: true });
  }, [editing]);
  const cancel = () => { backToEdit.current = true; setEditing(false); setValue(msg.text); };
  if (editing) {
    const submit = () => {
      const changed = !!value.trim() && value.trim() !== msg.text;
      if (!changed) return cancel();
      // A real edit becomes a new version (new row) and starts an answer: focus goes to the composer, like after sending.
      const anchor = rowRef.current;
      setEditing(false);
      onEdit?.(value);
      refocusInRow(anchor, () => null, 0);
    };
    return (
      <div className="flex flex-col items-end" data-testid="msg-user-editing" ref={rowRef}>
        <div className="w-full rounded-[18px] bg-muted px-4 py-3">
          <textarea autoFocus value={value} aria-label="Edit message" onChange={(e) => setValue(e.target.value)} rows={Math.min(8, value.split('\n').length + 1)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); cancel(); } }}
            className="w-full resize-none bg-transparent leading-normal outline-none" />
          <div className="mt-2 flex justify-end gap-2 font-sans text-sm">
            <button type="button" onClick={cancel} className="rounded-full border border-line bg-bg px-3.5 py-1.5 hover:bg-hover">Cancel</button>
            <button type="button" onClick={submit} disabled={!value.trim()} className="rounded-full bg-fg px-3.5 py-1.5 text-bg disabled:opacity-40">Send</button>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="group relative flex flex-col items-end" data-testid="msg-user">
      <UserText text={msg.text} />
      {/* Hover actions overlay the space under the bubble instead of reserving a row, so the answer starts
          right below the question (ChatGPT/Claude spacing). With version arrows the row stays in flow. */}
      <div className={clsx('flex items-center gap-0.5 rounded-full bg-bg transition-opacity group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100', version ? 'mt-1 opacity-100' : 'absolute right-0 top-full z-10 mt-0.5 opacity-0 [@media(hover:none)]:static [@media(hover:none)]:mt-1')} data-testid="user-actions" ref={actionsRef}>
        <CopyButton text={msg.text} />
        {onEdit && <IconButton label="Edit message" disabled={disabled} onClick={() => { setValue(msg.text); setEditing(true); }}><Pencil size={15} /></IconButton>}
        {version && onVersion && <VersionNav version={version} onVersion={onVersion} disabled={disabled} />}
      </div>
    </div>
  );
});

/** Print (or save as PDF) one question + answer, not the whole chat: the rest of the thread is hidden
 *  by `body[data-print-one]` rules in index.css until the print dialog closes. */
export function printOne(answer: HTMLElement | null) {
  const row = answer?.closest<HTMLElement>('[data-message-id]');
  if (!row) return window.print();
  const keep = [row, row.previousElementSibling].filter((e): e is HTMLElement => e instanceof HTMLElement && e.hasAttribute('data-message-id'));
  keep.forEach((e) => e.setAttribute('data-print-keep', ''));
  document.body.setAttribute('data-print-one', '');
  const done = () => { document.body.removeAttribute('data-print-one'); keep.forEach((e) => e.removeAttribute('data-print-keep')); window.removeEventListener('afterprint', done); };
  window.addEventListener('afterprint', done);
  window.print();
  // Some engines (and headless tests) never fire afterprint; print() blocks until the dialog closes, so tidy up now.
  setTimeout(done, 0);
}

/** "‹ 2/3 ›": under an edited question, or under a regenerated answer (QA-A18). `onVersion(k)` takes a 0-based index. */
function VersionNav({ version, onVersion, disabled }: { version: { current: number; total: number }; onVersion: (k: number) => void; disabled?: boolean }) {
  // QA-A21: the switched-to version is another message (its row remounts); keep focus on the same arrow,
  // or the other one when the end is reached.
  const go = (e: React.MouseEvent<HTMLButtonElement>, k: number, label: string) => {
    const from = e.currentTarget;
    const user = !!from.closest('[data-testid="msg-user"]');
    onVersion(k);
    refocusInRow(from, (row) => {
      const scope = row.querySelector<HTMLElement>(user ? '[data-testid="msg-user"] [data-testid="version-nav"]' : '[data-testid="msg-assistant"] [data-testid="version-nav"]');
      const other = label === 'Previous version' ? 'Next version' : 'Previous version';
      return enabled(scope?.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)) ?? enabled(scope?.querySelector<HTMLButtonElement>(`button[aria-label="${other}"]`));
    });
  };
  return (
    <div className="flex items-center font-sans text-xs text-fg-muted" data-testid="version-nav">
      <IconButton label="Previous version" className="h-7 w-7" disabled={disabled || version.current <= 1} onClick={(e) => go(e, version.current - 2, 'Previous version')}><ChevronLeft size={15} /></IconButton>
      <span className="tabular-nums">{version.current}/{version.total}</span>
      <IconButton label="Next version" className="h-7 w-7" disabled={disabled || version.current >= version.total} onClick={(e) => go(e, version.current, 'Next version')}><ChevronRight size={15} /></IconButton>
    </div>
  );
}

/** Regenerate menu styles (contract 1.10.0 `regenerate.style`): re-run in place as the next version, no follow-up turn. */
export type Refine = Exclude<RegenerateStyle, 'default'>;
/** Version label for a styled regeneration (`message.start.regenerateStyle` / `StoredMessage.regenerateStyle`). */
export const STYLE_LABEL: Record<Refine, string> = { concise: 'Concise', detailed: 'Detailed' };

export const AssistantTurn = memo(function AssistantTurn({ msg: view, isLast, onOpen, onRegenerate, onEdit, onSend, onReply, onRefine, version, onVersion, versionBusy, readOnly, sqlWithheld }: { msg: AnyView; isLast: boolean; onOpen: (a: Artifact) => void; onRegenerate: () => void; onEdit: (t: string) => void; onSend?: (t: string) => void; onReply?: OnReply; onRefine?: (style: Refine) => void; version?: { current: number; total: number }; onVersion?: (k: number) => void; versionBusy?: boolean; /** Shared /shared/:id view: no actions, feedback, retry, edits or replies. */ readOnly?: boolean; /** Shared without data (contract 1.15.0): SQL was withheld; tables say "SQL not shared". */ sqlWithheld?: boolean }) {
  const msg = view as AssistantView; // legacy part renderers; v1-only data is read through `view`
  const regenStyle = (view as { regenerateStyle?: RegenerateStyle }).regenerateStyle;
  const styleLabel = regenStyle && regenStyle !== 'default' ? STYLE_LABEL[regenStyle] : undefined;
  const reply: OnReply | undefined = onReply ?? (onSend ? (t) => onSend(t) : undefined);
  // Feedback survives remounts (virtualized rows) in memory; see lib/feedback.ts.
  const [vote, setVoteState] = useState<Vote | null>(() => feedbackFor(view.id)?.vote ?? storedVote((view as { feedback?: unknown }).feedback));
  const setVote = (v: Vote | null) => { setVoteState(v); recordFeedback({ messageId: view.id, vote: v }); };
  const regen = useMenu();
  const more = useMenu();
  const toast = useToast();
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [sqlOpen, setSqlOpen] = useState(false);
  const open = (part: Artifact['part'], title?: string, version?: number) => onOpen({ part, title: title ?? ((('title' in part && part.title) || (part.type === 'image' ? part.alt : 'Artifact')) as string), version, related: parts as typeof msg.parts });

  // All steps (v1 step.* events and legacy tool_call parts) render as one live timeline (Claude/ChatGPT).
  const parts = partsOf(view);
  const steps = stepsOf(view);
  const usage = usageOf(view);
  const queries = queriesOf(view);
  const okQueries = queries.filter((q) => q.state !== 'error' && q.sql.trim()).length;
  const escalations = escalationsOf(view);
  const tables = parts.filter((p): p is TablePart => p.type === 'table');
  const linkedSql = new Set(tables.map((t) => t.sqlPartId).filter(Boolean));
  const sqlRunning = msg.streaming && steps.some((s) => s.state === 'running' && (s.kind === 'sql' || s.kind === 'repair'));
  // Live until the answer starts after the last step; then it collapses to its summary line.
  const ANSWER = ['text', 'table', 'chart', 'image', 'abstain', 'clarification', 'error'];
  const lastTool = parts.map((p) => p.type).lastIndexOf('tool_call');
  const answering = parts.some((p, i) => i > lastTool && ANSWER.includes(p.type));
  const timelineLive = msg.streaming && !answering;
  // QA-A10: reasoning folds into the timeline, so the answer has one "Thought for 1.2s" row, not two.
  const reasoningParts = parts.filter((p): p is Extract<typeof p, { type: 'reasoning' }> => p.type === 'reasoning');
  // QA-A15: one "Thinking…" row (icon + timer) from the moment the question is sent, on live and mock alike.
  // The same element (key "timeline") then carries the steps, so nothing swaps in place as events arrive.
  const hasTimeline = steps.length > 0 || reasoningParts.length > 0 || timelineLive;
  const reasoning = hasTimeline ? reasoningParts : [];
  const timeline = hasTimeline ? <StepTimeline key="timeline" steps={steps} live={timelineLive} elapsedMs={usage?.elapsedMs} reasoning={reasoning} /> : null;
  const blocks: ReactNode[] = [];
  let timelineDone = false;
  const placeTimeline = () => {
    if (timelineDone) return;
    timelineDone = true;
    if (timeline) blocks.push(timeline);
    escalations.forEach((e, i) => blocks.push(<EscalationNote key={`esc-${i}`} ev={e} />));
  };
  for (const p of parts) {
    if (p.type === 'tool_call') { placeTimeline(); continue; }
    // v1 steps aren't parts: the timeline goes before the first non-reasoning part.
    if (p.type !== 'reasoning' && p.type !== 'status') placeTimeline();
    if (p.type === 'reasoning' && timeline) continue; // shown inside the timeline
    if (p.type === 'code' && p.role === 'query' && linkedSql.has(p.id)) continue; // shown under its table
    if (p.type === 'abstain') { blocks.push(<AbstainCard key={p.id} part={p} onReply={readOnly ? undefined : reply} />); continue; }
    if (p.type === 'clarification') { blocks.push(<ClarifyCard key={p.id} part={p} onReply={readOnly ? undefined : reply} active={!readOnly && isLast && !msg.streaming} />); continue; }
    blocks.push(<PartView key={p.id} part={p as Part} msg={msg} open={open} openArtifact={onOpen} onRetry={readOnly ? undefined : onRegenerate} onEdit={readOnly ? undefined : onEdit} />);
    if (p.type === 'table') {
      const q = queryForTable(view, p);
      if (q) blocks.push(<TableSql key={`sql-${p.id}`} query={q} />);
      else if (sqlWithheld) blocks.push(<SqlWithheldNote key={`sql-${p.id}`} />);
    }
  }
  placeTimeline();
  if (sqlRunning) blocks.push(<TableSkeleton key="skeleton" />);

  const rootRef = useRef<HTMLDivElement>(null);
  // QA-A22: a failed turn already has its Retry on the error card; the bar keeps only Copy (ChatGPT/Claude).
  const cardRetry = parts.some((p) => p.type === 'error' && p.retryable);
  const failed = parts.some((p) => p.type === 'error') && !parts.some((p) => ['text', 'table', 'chart', 'image', 'artifact'].includes(p.type));
  const empty = parts.length === 0 && steps.length === 0;
  const nQueries = usage?.queries ?? queries.length;
  return (
    <div className="group" data-testid="msg-assistant" ref={rootRef}>
      {blocks}
      {/* QA-A7: while the timeline is live it already says what is happening; only transport states (offline, reconnecting, 429) add a line. */}
      {msg.streaming && (msg.status || empty) && !(timeline && timelineLive && !isTransportStatus(msg.status)) && (
        <div className="my-2 flex items-center gap-2 font-sans text-sm text-fg-muted first:mt-0" data-testid="status">
          {empty && !msg.status ? <span className="streaming-caret prose-chat" /> : <span className="shimmer" aria-hidden={isTransportStatus(msg.status) || undefined}>{msg.status}</span>}
        </div>
      )}
      {isLast && <TransportAnnouncer status={msg.streaming ? msg.status : null} />}
      {!msg.streaming && msg.finishReason === 'aborted' && <StoppedNote onRetry={isLast && !readOnly ? onRegenerate : undefined} />}
      {/* A failed turn used no data for an answer: no Sources chip. */}
      {!msg.streaming && !failed && <SourcesChip sources={(view as { sources?: Source[] }).sources} parts={parts} />}
      {!readOnly && !msg.streaming && !empty && (
        <div className={clsx('-ml-2 mt-2 flex flex-wrap items-center gap-0.5 transition-opacity', isLast ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100')} data-testid="msg-actions">
          {version && onVersion && <VersionNav version={version} onVersion={onVersion} disabled={versionBusy} />}
          <CopyButton text={() => messageMarkdown(view)} tip />
          {failed && isLast && !cardRetry && <IconButton tip label="Regenerate" onClick={onRegenerate}><RefreshCw size={16} /></IconButton>}
          {!failed && (<>
          <IconButton tip label="Good response" aria-pressed={vote === 'up'} onClick={() => setVote(vote === 'up' ? null : 'up')}><ThumbsUp size={16} fill={vote === 'up' ? 'currentColor' : 'none'} /></IconButton>
          <IconButton tip label="Bad response" aria-pressed={vote === 'down'} onClick={() => { const next = vote === 'down' ? null : 'down'; setVote(next); setFeedbackOpen(next === 'down'); setFeedbackSent(false); }}><ThumbsDown size={16} fill={vote === 'down' ? 'currentColor' : 'none'} /></IconButton>
          {/* One click regenerates (the earlier answer stays as version 1/2, QA-A18); the chevron holds the styles. */}
          {isLast && <IconButton tip label="Regenerate" onClick={onRegenerate}><RefreshCw size={16} /></IconButton>}
          {isLast && onRefine && <IconButton tip label="Answer style" className="-ml-1 w-6!" aria-haspopup="menu" aria-expanded={!!regen.anchor} onClick={regen.toggle} data-testid="regenerate-menu-button"><ChevronDown size={14} /></IconButton>}
          <IconButton tip label="More actions" aria-haspopup="menu" aria-expanded={!!more.anchor} onClick={more.toggle} data-testid="more-actions"><MoreHorizontal size={16} /></IconButton>
          {styleLabel && <span className="mx-1 font-sans text-xs text-fg-subtle" data-testid="regen-style" title="Regenerated with this style">{styleLabel}</span>}
          <AnswerMeta usage={usage && (usage.queries > 0 || usage.costUsd !== undefined) ? usage : undefined} queries={queries.length ? nQueries : 0} repairs={steps.filter((s) => s.kind === 'repair' && s.state === 'ok').length} onSql={queries.length ? () => setSqlOpen((o) => !o) : undefined} sqlOpen={sqlOpen} />
          </>)}
          {feedbackSent && <span className="ml-2 font-sans text-xs text-fg-subtle">Thanks for the feedback</span>}
        </div>
      )}
      {regen.anchor && onRefine && (
        <Menu anchor={regen.anchor} onClose={regen.close} label="Regenerate" items={[
          { label: 'Try again', icon: <RefreshCw size={15} />, onSelect: onRegenerate },
          'sep',
          { label: 'More concise', icon: <Minimize2 size={15} />, onSelect: () => onRefine('concise') },
          { label: 'More detailed', icon: <Plus size={15} />, onSelect: () => onRefine('detailed') },
        ]} />
      )}
      {more.anchor && (
        <Menu anchor={more.anchor} onClose={more.close} label="More actions" items={[
          { label: 'Print this answer', icon: <Printer size={15} />, onSelect: () => printOne(rootRef.current) },
          { label: 'Copy as Markdown', icon: <FileText size={15} />, onSelect: () => { void copyText(messageMarkdown(view)).then((ok) => ok && toast({ text: 'Copied as Markdown' })); } },
          ...(okQueries ? [{ label: okQueries > 1 ? `Copy SQL (${okQueries} queries)` : 'Copy SQL', icon: <Code2 size={15} />, onSelect: () => { void copyText(sqlForClipboard(queries)).then((ok) => ok && toast({ text: 'SQL copied' })); } } as MenuItem] : []),
        ]} />
      )}
      {sqlOpen && <SqlInspector queries={queries} onClose={() => setSqlOpen(false)} />}
      {feedbackOpen && <FeedbackPanel onClose={() => setFeedbackOpen(false)} onSubmit={(f) => { recordFeedback({ messageId: view.id, vote: 'down', reasons: f.reasons, detail: f.detail }); setFeedbackOpen(false); setFeedbackSent(true); }} />}
    </div>
  );
});

/** Polite live region for transport states (429 countdown, offline, reconnecting), throttled by transportAnnouncement. */
function TransportAnnouncer({ status }: { status: string | null | undefined }) {
  const prev = useRef<string | null | undefined>(null);
  const [said, setSaid] = useState('');
  useEffect(() => {
    const next = transportAnnouncement(status, prev.current);
    prev.current = status;
    if (next) setSaid(next);
    else if (!isTransportStatus(status)) setSaid('');
  }, [status]);
  return <span role="status" aria-live="polite" aria-atomic="true" className="sr-only" data-testid="transport-announcer">{said}</span>;
}

/**
 * Thumbs-down follow-up (as in ChatGPT/Claude): reason chips (Capabilities.feedbackReasons, contract
 * 1.7.0; ids sent, labels shown) plus an optional comment (≤ 1000 chars). The comment is user text:
 * it goes only to the feedback endpoint, never to logs, URLs or storage.
 */
function FeedbackPanel({ onClose, onSubmit }: { onClose: () => void; onSubmit: (f: { reasons: string[]; detail: string }) => void }) {
  const [reasons, setReasons] = useState<string[]>([]);
  const [detail, setDetail] = useState('');
  const toggle = (r: string) => setReasons((rs) => (rs.includes(r) ? rs.filter((x) => x !== r) : [...rs, r]));
  const ref = useRef<HTMLDivElement>(null);
  // QA-A5: the panel opens under the last answer, often below the fold. Bring it into view and put focus on the first reason.
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduce = typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    el.scrollIntoView?.({ block: 'nearest', behavior: reduce ? 'auto' : 'smooth' });
    el.querySelector<HTMLButtonElement>('button[aria-pressed]')?.focus({ preventScroll: true });
  }, []);
  return ( // oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- Escape delegated from focused children
    <div ref={ref} className="mt-2 scroll-mb-28 rounded-xl border border-line bg-bg p-3 font-sans text-sm" data-testid="feedback-panel" role="group" aria-label="Tell us more"
      onKeyDown={(e) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } }}>
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium">Tell us more</span>
        <IconButton label="Close feedback" className="h-7 w-7" onClick={onClose}>×</IconButton>
      </div>
      <div className="flex flex-wrap gap-2">
        {feedbackReasons('down').map((r) => (
          <button key={r.id} type="button" aria-pressed={reasons.includes(r.id)} onClick={() => toggle(r.id)} data-reason={r.id}
            className={clsx('rounded-full border px-3 py-1.5 text-[13px]', reasons.includes(r.id) ? 'border-fg bg-hover' : 'border-line hover:bg-hover')}>{r.label}</button>
        ))}
      </div>
      <textarea value={detail} onChange={(e) => setDetail(e.target.value)} rows={2} maxLength={MAX_FEEDBACK_COMMENT} aria-label="Feedback detail" placeholder="What should the answer have been? (optional, no patient identifiers)"
        className="mt-3 w-full resize-none rounded-lg border border-line bg-bg px-3 py-2 outline-none focus:border-fg-subtle" />
      <div className="mt-2 flex justify-end">
        <button type="button" disabled={!reasons.length && !detail.trim()} onClick={() => onSubmit({ reasons, detail })} className="rounded-full bg-fg px-3.5 py-1.5 text-bg disabled:opacity-40">Submit</button>
      </div>
    </div>
  );
}

function PartView({ part, msg, open, openArtifact, onRetry, onEdit }: { part: Part; msg: AssistantView; open: (p: Artifact['part'], title?: string, v?: number) => void; openArtifact: (a: Artifact) => void; onRetry?: () => void; onEdit?: (t: string) => void }) {
  switch (part.type) {
    case 'text': return <Markdown text={part.text} streaming={msg.openText.includes(part.id)} />;
    case 'reasoning': return <ReasoningView part={part} running={msg.streaming && part.durationMs === undefined} />;
    case 'status': return null;
    case 'table': return <DataTable part={part} onOpen={() => open(part)} />;
    case 'chart': return <ChartView part={part} onOpen={() => open(part)} />;
    case 'code': return <CodePartView part={part} onOpen={() => open(part)} />;
    case 'image': return <ImageView part={part} onOpen={() => open(part)} />;
    case 'file': {
      const table = (partsOf(msg) as typeof msg.parts).find((p): p is TablePart => p.type === 'table' && p.csvFileId === part.fileId);
      return <FileCard part={part} onPreview={part.previewable && table ? () => open(table) : undefined} />;
    }
    case 'callout': return <Callout part={part} onEdit={onEdit} />;
    case 'error': return <ErrorView part={part} onRetry={onRetry} />;
    case 'artifact':
      return <ArtifactCard part={part} onOpen={() => openArtifact(toArtifact(part, partsOf(msg) as typeof msg.parts))} />;
    default:
      return KNOWN_PART_TYPES.includes((part as Part).type) ? null : <Unsupported part={part as Part} />;
  }
}

export { tableToTsv };
