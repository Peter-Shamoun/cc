import { useState, type ReactNode } from 'react';
import { ChevronLeft, ChevronRight, Pencil, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-react';
import clsx from 'clsx';
import type { AssistantView, ChartPart, CodePart, ImagePart, Part, TablePart, ToolCallPart, UserMessage } from '../lib/contract';
import { KNOWN_PART_TYPES } from '../lib/contract';
import { Markdown } from './parts/Markdown';
import { CodePartView } from './parts/CodeBlock';
import { DataTable } from './parts/DataTable';
import { ChartView } from './parts/ChartView';
import { ArtifactCard, Callout, ErrorView, FileCard, ImageView, ReasoningView, ToolGroup, Unsupported } from './parts/misc';
import { CopyButton, IconButton } from './ui';
import { tableToTsv, visible } from '../lib/csv';

export type Artifact = { part: TablePart | ChartPart | CodePart | ImagePart; title: string; version?: number; related: Part[] };

/** Markdown export for Copy (§4.12): text, tables as GFM (PHI columns dropped), SQL, callouts. */
export function messageMarkdown(m: AssistantView): string {
  return m.parts
    .map((p) => {
      switch (p.type) {
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

export function UserBubble({ msg, onEdit, version, onVersion, disabled }: { msg: UserMessage; onEdit?: (t: string) => void; version?: { current: number; total: number }; onVersion?: (k: number) => void; disabled?: boolean }) {
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
      <div className={clsx('mt-1 flex items-center gap-0.5 transition-opacity group-hover:opacity-100 focus-within:opacity-100', version ? 'opacity-100' : 'opacity-0')}>
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
}

export function AssistantTurn({ msg, isLast, onOpen, onRegenerate, onEdit }: { msg: AssistantView; isLast: boolean; onOpen: (a: Artifact) => void; onRegenerate: () => void; onEdit: (t: string) => void }) {
  const [vote, setVote] = useState<'up' | 'down' | null>(msg.feedback ?? null);
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackSent, setFeedbackSent] = useState(false);
  const open = (part: Artifact['part'], title?: string, version?: number) => onOpen({ part, title: title ?? ((('title' in part && part.title) || (part.type === 'image' ? part.alt : 'Artifact')) as string), version, related: msg.parts });

  // Group consecutive tool calls into one disclosure.
  const blocks: ReactNode[] = [];
  let tools: ToolCallPart[] = [];
  const flush = () => {
    if (tools.length) blocks.push(<ToolGroup key={`tg-${tools[0].id}`} parts={tools} />);
    tools = [];
  };
  for (const p of msg.parts) {
    if (p.type === 'tool_call') { tools.push(p); continue; }
    flush();
    blocks.push(<PartView key={p.id} part={p} msg={msg} open={open} onRetry={onRegenerate} onEdit={onEdit} />);
  }
  flush();

  const empty = msg.parts.length === 0;
  return (
    <div className="group" data-testid="msg-assistant" aria-live={msg.streaming ? 'polite' : undefined} aria-busy={msg.streaming}>
      {blocks}
      {msg.streaming && (msg.status || empty) && (
        <div className="my-2 flex items-center gap-2 font-sans text-sm text-fg-muted" data-testid="status">
          {empty && !msg.status ? <span className="streaming-caret prose-chat" /> : <span className="shimmer">{msg.status}</span>}
        </div>
      )}
      {!msg.streaming && !empty && (
        <div className={clsx('-ml-2 mt-2 flex items-center gap-0.5 transition-opacity', isLast ? 'opacity-100' : 'opacity-0 group-hover:opacity-100 focus-within:opacity-100')} data-testid="msg-actions">
          <CopyButton text={() => messageMarkdown(msg)} />
          <IconButton label="Good response" aria-pressed={vote === 'up'} onClick={() => setVote(vote === 'up' ? null : 'up')}><ThumbsUp size={16} fill={vote === 'up' ? 'currentColor' : 'none'} /></IconButton>
          <IconButton label="Bad response" aria-pressed={vote === 'down'} onClick={() => { const next = vote === 'down' ? null : 'down'; setVote(next); setFeedbackOpen(next === 'down'); setFeedbackSent(false); }}><ThumbsDown size={16} fill={vote === 'down' ? 'currentColor' : 'none'} /></IconButton>
          {isLast && <IconButton label="Regenerate" onClick={onRegenerate}><RefreshCw size={16} /></IconButton>}
          {msg.usage && msg.usage.queries > 0 && <span className="ml-2 font-sans text-xs text-fg-subtle">{msg.usage.queries} {msg.usage.queries === 1 ? 'query' : 'queries'} · {(msg.usage.elapsedMs / 1000).toFixed(1)} s</span>}
          {msg.finishReason === 'aborted' && <span className="ml-2 font-sans text-xs text-fg-subtle">Stopped</span>}
          {feedbackSent && <span className="ml-2 font-sans text-xs text-fg-subtle">Thanks for the feedback</span>}
        </div>
      )}
      {feedbackOpen && <FeedbackPanel onClose={() => setFeedbackOpen(false)} onSubmit={() => { setFeedbackOpen(false); setFeedbackSent(true); }} />}
    </div>
  );
}

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
  return (
    <div className="mt-2 rounded-xl border border-line bg-bg p-3 font-sans text-sm" data-testid="feedback-panel" role="group" aria-label="Tell us more">
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
