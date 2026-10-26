import { memo, useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight, Code2, FileText, Minimize2, MoreHorizontal, Pencil, Plus, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-react';
import clsx from 'clsx';
import type { AssistantView, ChartPart, CodePart, ImagePart, Part, TablePart, UserMessage } from '../lib/contract';
import { KNOWN_PART_TYPES } from '../lib/contract';
import { Markdown } from './parts/Markdown';
import { CodePartView } from './parts/CodeBlock';
import { DataTable } from './parts/DataTable';
import { ChartView } from './parts/ChartView';
import { ArtifactCard, Callout, ErrorView, FileCard, ImageView, ReasoningView, Unsupported } from './parts/misc';
import { CopyButton, IconButton } from './ui';
import { StepTimeline } from './answer/StepTimeline';
import { queriesOf, queryForTable, SqlInspector, TableSql } from './answer/SqlInspector';
import { AbstainCard, ClarifyCard, EscalationNote, StoppedNote, TableSkeleton } from './answer/States';
import { AnswerMeta } from './answer/AnswerMeta';
import { escalationsOf, partsOf, stepsOf, usageOf, type AnyView } from './answer/normalize';
import type { OnReply } from './answer/States';
import { copyText, tableToTsv, visible } from '../lib/csv';
import { feedbackFor, recordFeedback, sqlForClipboard, type Vote } from '../lib/feedback';
import { Menu, useMenu, useToast, type MenuItem } from '../shell/primitives';
import { isTransportStatus } from '../lib/useChat';

export type Artifact = { part: TablePart | ChartPart | CodePart | ImagePart; title: string; version?: number; related: Part[] };

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
          return [`| ${cols.map(([c]) => c.label).join(' | ')} |`, `|${cols.map(() => '---').join('|')}|`, ...p.rows.map((r) => `| ${cols.map(([, i]) => String(r[i] ?? '')).join(' | ')} |`)].join('\n');
        }
        default: return '';
      }
    })
    .filter(Boolean)
    .join('\n\n');
}

export const UserBubble = memo(function UserBubble({ msg, onEdit, version, onVersion, disabled }: { msg: UserMessage; onEdit?: (t: string) => void; version?: { current: number; total: number }; onVersion?: (k: number) => void; disabled?: boolean }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(msg.text);
  if (editing) {
    const submit = () => { setEditing(false); if (value.trim() && value.trim() !== msg.text) onEdit?.(value); };
    return (
      <div className="flex flex-col items-end" data-testid="msg-user-editing">
        <div className="w-full rounded-[18px] bg-muted px-4 py-3">
          <textarea autoFocus value={value} aria-label="Edit message" onChange={(e) => setValue(e.target.value)} rows={Math.min(8, value.split('\n').length + 1)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } if (e.key === 'Escape') setEditing(false); }}
            className="w-full resize-none bg-transparent leading-normal outline-none" />
          <div className="mt-2 flex justify-end gap-2 font-sans text-sm">
            <button type="button" onClick={() => { setEditing(false); setValue(msg.text); }} className="rounded-full border border-line bg-bg px-3.5 py-1.5 hover:bg-hover">Cancel</button>
            <button type="button" onClick={submit} disabled={!value.trim()} className="rounded-full bg-fg px-3.5 py-1.5 text-bg disabled:opacity-40">Send</button>
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="group flex flex-col items-end" data-testid="msg-user">
      <div className="max-w-[70%] whitespace-pre-wrap rounded-[18px] bg-muted px-4 py-2.5 leading-normal">{msg.text}</div>
      <div className={clsx('mt-1 flex items-center gap-0.5 transition-opacity group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100', version ? 'opacity-100' : 'opacity-0')}>
        <CopyButton text={msg.text} />
        {onEdit && <IconButton label="Edit message" disabled={disabled} onClick={() => { setValue(msg.text); setEditing(true); }}><Pencil size={15} /></IconButton>}
        {version && onVersion && (
          <div className="flex items-center font-sans text-xs text-fg-muted" data-testid="version-nav">
            <IconButton label="Previous version" className="h-7 w-7" disabled={disabled || version.current <= 1} onClick={() => onVersion(version.current - 2)}><ChevronLeft size={15} /></IconButton>
            <span className="tabular-nums">{version.current}/{version.total}</span>
            <IconButton label="Next version" className="h-7 w-7" disabled={disabled || version.current >= version.total} onClick={() => onVersion(version.current)}><ChevronRight size={15} /></IconButton>
          </div>
        )}
      </div>
    </div>
  );
});

/** Regenerate menu styles. Until the contract carries a style on `regenerate`, these ask as a visible follow-up. */
export type Refine = 'concise' | 'detailed';
export const REFINE_PROMPT: Record<Refine, string> = { concise: 'Make that answer more concise.', detailed: 'Explain that answer in more detail.' };

export const AssistantTurn = memo(function AssistantTurn({ msg: view, isLast, onOpen, onRegenerate, onEdit, onSend, onReply, onRefine }: { msg: AnyView; isLast: boolean; onOpen: (a: Artifact) => void; onRegenerate: () => void; onEdit: (t: string) => void; onSend?: (t: string) => void; onReply?: OnReply; onRefine?: (style: Refine) => void }) {
  const msg = view as AssistantView; // legacy part renderers; v1-only data is read through `view`
  const reply: OnReply | undefined = onReply ?? (onSend ? (t) => onSend(t) : undefined);
  // Feedback survives remounts (virtualized rows) in memory; see lib/feedback.ts.
  const [vote, setVoteState] = useState<Vote | null>(() => feedbackFor(view.id)?.vote ?? msg.feedback ?? null);
  const setVote = (v: Vote | null) => { setVoteState(v); recordFeedback({ messageId: view.id, vote: v }); };
  const regen = useMenu();
  const more = useMenu();
  const toast = useToast();
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);
  const [sqlOpen, setSqlOpen] = useState(false);
  const open = (part: Artifact['part'], title?: string, version?: number) => onOpen({ part, title: title ?? ((('title' in part && part.title) || (part.type === 'image' ? part.alt : 'Artifact')) as string), version, related: msg.parts });

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
  const reasoning = steps.length > 0 ? parts.filter((p): p is Extract<typeof p, { type: 'reasoning' }> => p.type === 'reasoning') : [];
  const timeline = steps.length > 0 ? <StepTimeline key="timeline" steps={steps} live={timelineLive} elapsedMs={usage?.elapsedMs} reasoning={reasoning} /> : null;
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
    if (p.type === 'abstain') { blocks.push(<AbstainCard key={p.id} part={p} onReply={reply} />); continue; }
    if (p.type === 'clarification') { blocks.push(<ClarifyCard key={p.id} part={p} onReply={reply} active={isLast && !msg.streaming} />); continue; }
    blocks.push(<PartView key={p.id} part={p as Part} msg={msg} open={open} onRetry={onRegenerate} onEdit={onEdit} />);
    if (p.type === 'table') {
      const q = queryForTable(view, p);
      if (q) blocks.push(<TableSql key={`sql-${p.id}`} query={q} />);
    }
  }
  placeTimeline();
  if (sqlRunning) blocks.push(<TableSkeleton key="skeleton" />);

  const empty = parts.length === 0 && steps.length === 0;
  const nQueries = usage?.queries ?? queries.length;
  return (
    <div className="group" data-testid="msg-assistant">
      {blocks}
      {/* QA-A7: while the timeline is live it already says what is happening; only transport states (offline, reconnecting, 429) add a line. */}
      {msg.streaming && (msg.status || empty) && !(timeline && timelineLive && !isTransportStatus(msg.status)) && (
        <div className="my-2 flex items-center gap-2 font-sans text-sm text-fg-muted" data-testid="status">
          {empty && !msg.status ? <span className="streaming-caret prose-chat" /> : <span className="shimmer">{msg.status}</span>}
        </div>
      )}
      {!msg.streaming && msg.finishReason === 'aborted' && <StoppedNote onRetry={isLast ? onRegenerate : undefined} />}
      {!msg.streaming && !empty && (
        <div className={clsx('-ml-2 mt-2 flex flex-wrap items-center gap-0.5 transition-opacity', isLast ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100 [@media(hover:none)]:opacity-100')} data-testid="msg-actions">
          <CopyButton text={() => messageMarkdown(view)} tip />
          <IconButton tip label="Good response" aria-pressed={vote === 'up'} onClick={() => setVote(vote === 'up' ? null : 'up')}><ThumbsUp size={16} fill={vote === 'up' ? 'currentColor' : 'none'} /></IconButton>
          <IconButton tip label="Bad response" aria-pressed={vote === 'down'} onClick={() => { const next = vote === 'down' ? null : 'down'; setVote(next); setFeedbackOpen(next === 'down'); setFeedbackSent(false); }}><ThumbsDown size={16} fill={vote === 'down' ? 'currentColor' : 'none'} /></IconButton>
          {isLast && (onRefine
            ? <IconButton tip label="Regenerate" aria-haspopup="menu" aria-expanded={!!regen.anchor} onClick={regen.toggle} data-testid="regenerate-menu-button"><RefreshCw size={16} /></IconButton>
            : <IconButton tip label="Regenerate" onClick={onRegenerate}><RefreshCw size={16} /></IconButton>)}
          <IconButton tip label="More actions" aria-haspopup="menu" aria-expanded={!!more.anchor} onClick={more.toggle} data-testid="more-actions"><MoreHorizontal size={16} /></IconButton>
          <AnswerMeta usage={usage && (usage.queries > 0 || usage.costUsd !== undefined) ? usage : undefined} queries={queries.length ? nQueries : 0} repairs={steps.filter((s) => s.kind === 'repair' && s.state === 'ok').length} onSql={queries.length ? () => setSqlOpen((o) => !o) : undefined} sqlOpen={sqlOpen} />
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
          { label: 'Copy as Markdown', icon: <FileText size={15} />, onSelect: () => { void copyText(messageMarkdown(view)).then((ok) => ok && toast({ text: 'Copied as Markdown' })); } },
          ...(okQueries ? [{ label: okQueries > 1 ? `Copy SQL (${okQueries} queries)` : 'Copy SQL', icon: <Code2 size={15} />, onSelect: () => { void copyText(sqlForClipboard(queries)).then((ok) => ok && toast({ text: 'SQL copied' })); } } as MenuItem] : []),
        ]} />
      )}
      {sqlOpen && <SqlInspector queries={queries} onClose={() => setSqlOpen(false)} />}
      {feedbackOpen && <FeedbackPanel onClose={() => setFeedbackOpen(false)} onSubmit={(f) => { recordFeedback({ messageId: view.id, vote: 'down', reasons: f.reasons, detail: f.detail }); setFeedbackOpen(false); setFeedbackSent(true); }} />}
    </div>
  );
});

const FEEDBACK_REASONS = ['Wrong numbers', 'Wrong or missing filter', 'Misread the question', 'SQL looks wrong', 'Chart not helpful', 'Too slow', 'Other'];

/**
 * Thumbs-down follow-up (as in ChatGPT/Claude): reason chips plus optional detail. The detail can
 * contain PHI, so v1 keeps it in component state only; a feedback endpoint with the same PHI
 * handling as chat text is a backend follow-up.
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
        {FEEDBACK_REASONS.map((r) => (
          <button key={r} type="button" aria-pressed={reasons.includes(r)} onClick={() => toggle(r)}
            className={clsx('rounded-full border px-3 py-1.5 text-[13px]', reasons.includes(r) ? 'border-fg bg-hover' : 'border-line hover:bg-hover')}>{r}</button>
        ))}
      </div>
      <textarea value={detail} onChange={(e) => setDetail(e.target.value)} rows={2} aria-label="Feedback detail" placeholder="What should the answer have been? (optional, no patient identifiers)"
        className="mt-3 w-full resize-none rounded-lg border border-line bg-bg px-3 py-2 outline-none focus:border-fg-subtle" />
      <div className="mt-2 flex justify-end">
        <button type="button" disabled={!reasons.length && !detail.trim()} onClick={() => onSubmit({ reasons, detail })} className="rounded-full bg-fg px-3.5 py-1.5 text-bg disabled:opacity-40">Submit</button>
      </div>
    </div>
  );
}

function PartView({ part, msg, open, onRetry, onEdit }: { part: Part; msg: AssistantView; open: (p: Artifact['part'], title?: string, v?: number) => void; onRetry: () => void; onEdit: (t: string) => void }) {
  switch (part.type) {
    case 'text': return <Markdown text={part.text} streaming={msg.openText.includes(part.id)} />;
    case 'reasoning': return <ReasoningView part={part} running={msg.streaming && part.durationMs === undefined} />;
    case 'status': return null;
    case 'table': return <DataTable part={part} onOpen={() => open(part)} />;
    case 'chart': return <ChartView part={part} onOpen={() => open(part)} />;
    case 'code': return <CodePartView part={part} onOpen={() => open(part)} />;
    case 'image': return <ImageView part={part} onOpen={() => open(part)} />;
    case 'file': {
      const table = msg.parts.find((p): p is TablePart => p.type === 'table' && p.csvFileId === part.fileId);
      return <FileCard part={part} onPreview={part.previewable && table ? () => open(table) : undefined} />;
    }
    case 'callout': return <Callout part={part} onEdit={onEdit} />;
    case 'error': return <ErrorView part={part} onRetry={onRetry} />;
    case 'artifact':
      return 'type' in part.content ? <ArtifactCard part={part} onOpen={() => open(part.content as Artifact['part'], part.title, part.version)} /> : <Markdown text={part.content.markdown} />;
    default:
      return KNOWN_PART_TYPES.includes((part as Part).type) ? null : <Unsupported part={part as Part} />;
  }
}

export { tableToTsv };
