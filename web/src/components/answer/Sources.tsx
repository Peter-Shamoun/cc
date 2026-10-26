import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { ChevronRight, Database, FileText, Sigma } from 'lucide-react';
import clsx from 'clsx';
import type { Source } from '../../api/contract';
import type { Part, TablePart } from '../../api/contract';

/**
 * Data-source citations (contract 1.8.0, CONTRACT.md §14), shown like ChatGPT's "Sources" pill / Claude's
 * citation chips: one pill under the answer, and a popover that lists each view the answer read with the
 * columns it used. Schema identifiers only, never row values: columns come from the linked table parts'
 * column labels, not from `rows`.
 */
export type SourceRow = Source & { columns: string[] };

/** Answer sources first, then explored. Without server sources (mock / pre-1.8 transcripts), fall back to the
 *  tables' `source.views`, so the chip still says where the numbers came from. */
export function sourceRows(sources: Source[] | undefined, parts: Part[]): SourceRow[] {
  const tables = parts.filter((p): p is TablePart => p.type === 'table');
  const columnsFor = (s: { partIds?: string[]; name: string }) => {
    const linked = tables.filter((t) => s.partIds?.includes(t.id) || (!s.partIds && t.source?.views.includes(s.name)));
    return [...new Set(linked.flatMap((t) => t.columns.map((c) => c.label)))];
  };
  const list: Source[] = sources?.length
    ? sources
    : [...new Set(tables.flatMap((t) => t.source?.views ?? []))].map((name, i) => ({ id: `src_local_${i}`, kind: 'view', name, label: name, role: 'answer' as const }));
  const rank = (s: Source) => (s.role === 'answer' ? 0 : 1);
  return [...list].sort((a, b) => rank(a) - rank(b)).map((s) => ({ ...s, columns: columnsFor(s) }));
}

const KIND_ICON: Record<string, typeof Database> = { view: Database, definition: Sigma, document: FileText };

export function SourcesChip({ sources, parts }: { sources: Source[] | undefined; parts: Part[] }) {
  const rows = sourceRows(sources, parts);
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const used = rows.filter((r) => r.role === 'answer');
  if (!rows.length) return null;
  const shown = used.length ? used : rows;
  const first = shown[0];
  return (
    <>
      <button type="button" aria-haspopup="dialog" aria-expanded={!!anchor} onClick={(e) => setAnchor(anchor ? null : e.currentTarget)}
        className="mt-1 inline-flex max-w-full items-center gap-1.5 rounded-md px-1.5 py-1 font-sans text-xs text-fg-muted hover:bg-hover hover:text-fg"
        data-testid="sources-chip">
        <Database size={12} aria-hidden className="shrink-0" />
        <span className="font-medium">Sources</span>
        <span className="truncate font-mono text-[11px]">{first.name}</span>
        {shown.length > 1 && <span className="shrink-0 tabular-nums text-fg-subtle">+{shown.length - 1}</span>}
      </button>
      {anchor && <SourcesPopover rows={rows} anchor={anchor} onClose={() => setAnchor(null)} />}
    </>
  );
}

function SourcesPopover({ rows, anchor, onClose }: { rows: SourceRow[]; anchor: HTMLElement; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    const r = anchor.getBoundingClientRect();
    const el = ref.current;
    const w = el?.offsetWidth ?? 340, h = el?.offsetHeight ?? 220;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
    let top = r.bottom + 6;
    if (top + h > window.innerHeight - 8) top = Math.max(8, r.top - h - 6);
    setPos({ top, left });
  }, [anchor]);
  useEffect(() => { if (pos) ref.current?.focus(); }, [pos === null]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const down = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node) && !anchor.contains(e.target as Node)) onClose(); };
    document.addEventListener('mousedown', down);
    return () => document.removeEventListener('mousedown', down);
  }, [anchor, onClose]);
  const close = () => { onClose(); anchor.focus(); };
  const answer = rows.filter((r) => r.role === 'answer');
  const explored = rows.filter((r) => r.role !== 'answer');
  return createPortal(
    // oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- Escape-to-close on the dialog container (APG dialog pattern)
    <div ref={ref} role="dialog" aria-labelledby={`${id}-t`} tabIndex={-1} data-testid="sources-popover" style={pos ?? { top: -9999, left: -9999 }}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } }}
      className="fixed z-[60] max-h-[min(24rem,calc(100vh-16px))] w-[min(22rem,calc(100vw-16px))] overflow-auto rounded-2xl border border-line bg-elev p-1.5 font-sans text-sm text-fg shadow-[0_8px_30px_rgba(0,0,0,0.16)] outline-none motion-safe:animate-[cc-pop_120ms_ease-out]">
      <div id={`${id}-t`} className="px-2.5 pb-1 pt-1.5 text-xs font-medium text-fg-subtle">{answer.length ? 'Data this answer used' : 'Data looked at'}</div>
      <ul className="space-y-0.5">{(answer.length ? answer : explored).map((r) => <SourceItem key={r.id} r={r} />)}</ul>
      {answer.length > 0 && explored.length > 0 && (
        <details className="group/exp mt-1 border-t border-line pt-1">
          <summary className="flex cursor-pointer list-none items-center gap-1 px-2.5 py-1.5 text-xs text-fg-subtle hover:text-fg [&::-webkit-details-marker]:hidden">
            Also looked at ({explored.length})<ChevronRight size={12} className="transition-transform group-open/exp:rotate-90" aria-hidden />
          </summary>
          <ul className="space-y-0.5">{explored.map((r) => <SourceItem key={r.id} r={r} />)}</ul>
        </details>
      )}
      <p className="px-2.5 pb-1 pt-1.5 text-[11px] text-fg-subtle">Table and column names only. No row values are shown here.</p>
    </div>,
    document.body,
  );
}

function SourceItem({ r }: { r: SourceRow }) {
  const Icon = KIND_ICON[r.kind] ?? Database;
  return (
    <li className="rounded-xl px-2.5 py-2 hover:bg-hover" data-testid="source-item" data-kind={r.kind}>
      <div className="flex items-center gap-2">
        <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md border border-line bg-bg"><Icon size={13} aria-hidden /></span>
        <span className="min-w-0">
          <span className="block truncate font-medium">{r.label}</span>
          {r.label !== r.name && <span className="block truncate font-mono text-[11px] text-fg-subtle">{r.name}</span>}
        </span>
      </div>
      {r.description && <p className="mt-1 pl-8 text-xs leading-relaxed text-fg-muted">{r.description}</p>}
      {r.columns.length > 0 && (
        <div className="mt-1.5 flex flex-wrap gap-1 pl-8" aria-label="Columns used">
          {r.columns.map((c) => <span key={c} className={clsx('rounded-md bg-muted px-1.5 py-0.5 text-[11px] text-fg-muted')}>{c}</span>)}
        </div>
      )}
    </li>
  );
}
