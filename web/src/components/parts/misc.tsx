import { useState } from 'react';
import { AlertTriangle, ChevronDown, ChevronRight, Download, FileSpreadsheet, Info, Lightbulb, Loader2, Maximize2, ShieldCheck, Wrench, Check, X, Database, List, BarChart3, ListChecks, Search, RotateCcw, Code2, Table2, Image as ImageIcon, FileText, Copy } from 'lucide-react';
import clsx from 'clsx';
import ReactMarkdown from 'react-markdown';
import type { ArtifactPart, CalloutPart, ErrorPart, FilePart, ImagePart, Part, ReasoningPart, ToolCallPart } from '../../lib/contract';
import { downloadBlob } from '../../lib/csv';
import { IconButton } from '../ui';
import { useAdapter, useBlobUrl } from '../../adapters/context';
import { CodeBlock } from './CodeBlock';
import { imageDisplayBox } from '../../render/imageFrame';

function fmtBytes(n: number) {
  return n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`;
}

/** Inline markdown only (bold, code, links) for callout items. */
const Inline = ({ text }: { text: string }) => (
  <ReactMarkdown allowedElements={['p', 'strong', 'em', 'code', 'a']} unwrapDisallowed components={{ p: ({ children }) => <>{children}</> }}>
    {text}
  </ReactMarkdown>
);

export function FileCard({ part, onPreview }: { part: FilePart; onPreview?: () => void }) {
  const adapter = useAdapter();
  const expired = part.expiresAt ? Date.parse(part.expiresAt) < Date.now() : false;
  const isCsv = part.mime === 'text/csv';
  const download = async () => {
    if (!adapter || expired) return;
    downloadBlob(part.name, await adapter.fetchBlob(part.fileId));
  };
  return (
    <div className="my-3 flex w-[300px] max-w-full items-center gap-3 rounded-xl border border-line bg-elev p-2 pr-2" data-testid="file-card">
      <button type="button" onClick={onPreview} disabled={!onPreview} className="flex min-w-0 flex-1 items-center gap-3 text-left">
        <span className={clsx('flex h-10 w-10 shrink-0 items-center justify-center rounded-lg text-white', isCsv ? 'bg-[#10a37f]' : 'bg-[#5b8def]')}>
          {isCsv ? <FileSpreadsheet size={20} /> : <FileText size={20} />}
        </span>
        <span className="min-w-0">
          <span className="block truncate text-sm font-medium">{part.name}</span>
          <span className="block text-xs text-fg-subtle">
            {expired ? 'Expired, re-run to regenerate' : <>{isCsv ? 'CSV' : 'File'}{part.rowCount !== undefined && ` · ${part.rowCount.toLocaleString()} rows`} · {fmtBytes(part.sizeBytes)}</>}
          </span>
        </span>
      </button>
      <IconButton label={`Download ${part.name}`} onClick={download} disabled={expired}><Download size={16} /></IconButton>
    </div>
  );
}

export function ImageView({ part, onOpen }: { part: ImagePart; onOpen?: () => void }) {
  const { url, err } = useBlobUrl(part.blobId);
  const [lightbox, setLightbox] = useState(false);
  const adapter = useAdapter();
  const box = imageDisplayBox(part.width, part.height);
  const frame = { width: box.width, height: box.height };
  return (
    <figure className="group/img relative my-4 max-w-full" data-testid="image" style={{ width: box.width }}>
      <div data-testid="image-frame" style={frame}>
        {err ? (
          <div className="flex h-full w-full items-center justify-center rounded-xl border border-line text-sm text-fg-subtle">Image unavailable</div>
        ) : url ? (
          <img src={url} alt={part.alt} onClick={() => setLightbox(true)} className="h-full w-full cursor-zoom-in rounded-xl border border-line bg-white object-contain" />
        ) : (
          <div className="h-full w-full animate-pulse rounded-xl bg-muted" data-testid="image-skeleton" />
        )}
      </div>
      <div className="absolute right-2 top-2 flex gap-1 opacity-0 transition-opacity group-hover/img:opacity-100">
        <IconButton label="Download image" className="bg-elev/90 shadow" onClick={async () => adapter && downloadBlob(`claimclarity_figure.${part.mime === 'image/svg+xml' ? 'svg' : 'png'}`, await adapter.fetchBlob(part.blobId))}><Download size={15} /></IconButton>
        <IconButton label="Expand image" className="bg-elev/90 shadow" onClick={onOpen ?? (() => setLightbox(true))}><Maximize2 size={14} /></IconButton>
      </div>
      {part.caption && <figcaption className="mt-1.5 text-xs text-fg-subtle">{part.caption}</figcaption>}
      {lightbox && url && (
        <div role="dialog" aria-label={part.alt} onClick={() => setLightbox(false)} onKeyDown={(e) => e.key === 'Escape' && setLightbox(false)} tabIndex={-1} className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-8">
          <img src={url} alt={part.alt} className="max-h-full max-w-full rounded-lg bg-white" />
          <IconButton label="Close" className="absolute right-4 top-4 bg-white/90 text-black" onClick={() => setLightbox(false)}><X size={18} /></IconButton>
        </div>
      )}
    </figure>
  );
}

const CALLOUT = {
  assumption: { icon: Lightbulb, bar: 'var(--callout-bar)' },
  info: { icon: Info, bar: 'var(--link)' },
  caveat: { icon: AlertTriangle, bar: 'var(--callout-bar)' },
  data_quality: { icon: AlertTriangle, bar: 'var(--callout-bar)' },
  phi_redaction: { icon: ShieldCheck, bar: 'var(--accent)' },
} as const;

export function Callout({ part, onEdit }: { part: CalloutPart; onEdit?: (text: string) => void }) {
  const { icon: Icon, bar } = CALLOUT[part.variant] ?? CALLOUT.info;
  const [open, setOpen] = useState(part.items.length <= 4);
  return (
    <div className="my-4 rounded-xl border-l-[3px] bg-[var(--callout-bg)] px-4 py-3 font-sans text-sm" style={{ borderLeftColor: bar }} role="note" data-testid="callout">
      <button type="button" onClick={() => setOpen((o) => !o)} className="mb-1 flex items-center gap-2 font-semibold" aria-expanded={open}>
        <Icon size={16} /> {part.title ?? part.variant}
        {part.items.length > 4 && (open ? <ChevronDown size={14} /> : <ChevronRight size={14} />)}
      </button>
      {open && (
        <ul className="list-disc space-y-1 pl-6 leading-relaxed">
          {part.items.map((it) => (
            <li key={it.id}>
              <Inline text={it.text} />
              {it.editable && onEdit && (
                <button type="button" onClick={() => onEdit('Instead, assume ')} className="ml-2 text-xs text-fg-muted underline underline-offset-2 hover:text-fg">Change</button>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function ReasoningView({ part, running }: { part: ReasoningPart; running: boolean }) {
  const [open, setOpen] = useState(false);
  const secs = part.durationMs ? Math.max(1, Math.round(part.durationMs / 1000)) : null;
  return (
    <div className="my-2 font-sans text-sm" data-testid="reasoning">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1 text-fg-muted hover:text-fg">
        <span className={clsx(running && 'shimmer')}>{running ? 'Thinking…' : `Thought for ${secs ?? 1} s`}</span>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {open && <div className="mt-1.5 border-l-2 border-line pl-4 leading-relaxed text-fg-muted">{part.summary}</div>}
    </div>
  );
}

const TOOL_ICON: Record<string, typeof Wrench> = { run_sql: Database, describe_view: List, list_views: List, make_chart: BarChart3, verify: ListChecks, search_examples: Search };

export function ToolRow({ part }: { part: ToolCallPart }) {
  const [open, setOpen] = useState(false);
  const Icon = TOOL_ICON[part.tool] ?? Wrench;
  const meta = [part.output?.rowCount !== undefined && `${part.output.rowCount.toLocaleString()} rows`, part.durationMs !== undefined && `${part.durationMs} ms`].filter(Boolean).join(' · ');
  return (
    <div className="font-sans text-sm" data-testid="tool-call" data-state={part.state}>
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="group inline-flex max-w-full items-center gap-2 rounded-md py-1 text-left text-fg-muted hover:text-fg">
        <Icon size={14} className="shrink-0" />
        <span className={clsx('truncate', part.state === 'running' && 'shimmer')}>{part.label}</span>
        {part.state === 'running' && <Loader2 size={13} className="shrink-0 animate-spin" />}
        {part.state === 'ok' && <Check size={13} className="shrink-0 text-[#10a37f]" />}
        {part.state === 'error' && <X size={13} className="shrink-0 text-danger" />}
        {meta && <span className="shrink-0 text-xs text-fg-subtle">{meta}</span>}
        {part.agent && <span className="shrink-0 rounded-full border border-line px-1.5 text-[11px] text-fg-subtle">{part.agent}</span>}
        {open ? <ChevronDown size={13} className="shrink-0" /> : <ChevronRight size={13} className="shrink-0" />}
      </button>
      {open && (
        <div className="mb-2 ml-1.5 border-l-2 border-line pl-4 text-fg-muted">
          {part.input?.sql && <CodeBlock code={part.input.sql} language="sql" title="input" />}
          {part.input?.args && <CodeBlock code={JSON.stringify(part.input.args, null, 2)} language="json" title="args" />}
          {part.output?.message && <div className="py-1">{part.output.message}</div>}
          <div className="py-1 text-xs text-fg-subtle">Scoped session · result rows are not shown here</div>
        </div>
      )}
    </div>
  );
}

/** Groups consecutive tool calls: "Used 3 tools ▸" when more than 2 (spec §4.8). */
export function ToolGroup({ parts }: { parts: ToolCallPart[] }) {
  const running = parts.some((p) => p.state === 'running');
  const [open, setOpen] = useState(false);
  if (parts.length <= 2) return <div className="my-2">{parts.map((p) => <ToolRow key={p.id} part={p} />)}</div>;
  return (
    <div className="my-2 font-sans text-sm">
      <button type="button" aria-expanded={open || running} onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1.5 py-1 text-fg-muted hover:text-fg">
        <Wrench size={14} /> <span className={clsx(running && 'shimmer')}>{running ? `Using ${parts.length} tools…` : `Used ${parts.length} tools`}</span>
        {open || running ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
      </button>
      {(open || running) && <div className="ml-1.5 border-l-2 border-line pl-3">{parts.map((p) => <ToolRow key={p.id} part={p} />)}</div>}
    </div>
  );
}

export function ErrorView({ part, onRetry }: { part: ErrorPart; onRetry?: () => void }) {
  const [details, setDetails] = useState(false);
  return (
    <div className="my-4 rounded-xl border border-line border-l-[3px] bg-elev px-4 py-3 font-sans text-sm" style={{ borderLeftColor: 'var(--danger)' }} role="alert" data-testid="error">
      <div className="flex items-center gap-2 font-semibold text-danger"><AlertTriangle size={16} /> {part.title}</div>
      <p className="mt-1 text-fg">{part.message}</p>
      <div className="mt-2 flex items-center gap-3 text-xs">
        {part.retryable && onRetry && (
          <button type="button" onClick={onRetry} className="inline-flex items-center gap-1 rounded-full border border-line px-3 py-1 hover:bg-hover"><RotateCcw size={12} /> Retry</button>
        )}
        {part.details && <button type="button" onClick={() => setDetails((d) => !d)} className="text-fg-muted underline underline-offset-2">{details ? 'Hide details' : 'Details'}</button>}
      </div>
      {details && part.details && <pre className="mt-2 whitespace-pre-wrap rounded-md bg-[var(--code-bg)] p-2 font-mono text-xs text-fg-muted">{part.details}</pre>}
    </div>
  );
}

const KIND_ICON = { table: Table2, chart: BarChart3, code: Code2, image: ImageIcon, report: FileText } as const;

/** Claude-style in-thread artifact card (spec §4.10). */
export function ArtifactCard({ part, onOpen }: { part: ArtifactPart; onOpen: () => void }) {
  const Icon = KIND_ICON[part.kind];
  return (
    <button type="button" onClick={onOpen} className="my-4 flex w-full max-w-md items-center gap-3 rounded-xl border border-line bg-elev p-3 text-left font-sans hover:border-line-strong hover:bg-hover" data-testid="artifact-card">
      <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-line bg-bg"><Icon size={18} /></span>
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{part.title}</span>
        <span className="block text-xs capitalize text-fg-subtle">{part.kind} · v{part.version} · Click to open</span>
      </span>
      <Copy size={14} className="hidden" />
    </button>
  );
}

export function Unsupported({ part }: { part: Part }) {
  return <span className="my-2 inline-block rounded-full border border-line px-2.5 py-1 font-sans text-xs text-fg-subtle" data-testid="unsupported">Unsupported content ({(part as { type: string }).type})</span>;
}
