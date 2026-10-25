import { useState, type ReactNode } from 'react';
import { Pencil, RefreshCw, ThumbsDown, ThumbsUp } from 'lucide-react';
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

export function UserBubble({ msg, onEdit }: { msg: UserMessage; onEdit?: (t: string) => void }) {
  return (
    <div className="group flex flex-col items-end" data-testid="msg-user">
      <div className="max-w-[70%] whitespace-pre-wrap rounded-[18px] bg-muted px-4 py-2.5 leading-normal">{msg.text}</div>
      <div className="mt-1 flex gap-0.5 opacity-0 transition-opacity group-hover:opacity-100 focus-within:opacity-100">
        <CopyButton text={msg.text} />
        {onEdit && <IconButton label="Edit message" onClick={() => onEdit(msg.text)}><Pencil size={15} /></IconButton>}
      </div>
    </div>
  );
}

export function AssistantTurn({ msg, isLast, onOpen, onRegenerate, onEdit }: { msg: AssistantView; isLast: boolean; onOpen: (a: Artifact) => void; onRegenerate: () => void; onEdit: (t: string) => void }) {
  const [vote, setVote] = useState<'up' | 'down' | null>(msg.feedback ?? null);
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
          <IconButton label="Bad response" aria-pressed={vote === 'down'} onClick={() => setVote(vote === 'down' ? null : 'down')}><ThumbsDown size={16} fill={vote === 'down' ? 'currentColor' : 'none'} /></IconButton>
          {isLast && <IconButton label="Regenerate" onClick={onRegenerate}><RefreshCw size={16} /></IconButton>}
          {msg.usage && <span className="ml-2 font-sans text-xs text-fg-subtle">{msg.usage.queries} {msg.usage.queries === 1 ? 'query' : 'queries'} · {(msg.usage.elapsedMs / 1000).toFixed(1)} s</span>}
          {msg.finishReason === 'aborted' && <span className="ml-2 font-sans text-xs text-fg-subtle">Stopped</span>}
        </div>
      )}
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
