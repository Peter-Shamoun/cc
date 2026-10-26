import { memo, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown, ArrowUp, Download, Lock, Maximize2, ShieldAlert } from 'lucide-react';
import clsx from 'clsx';
import type { TablePart } from '../../lib/contract';
import { csvFilename, downloadBlob, downloadText, formatCell, isNumeric, tableToCsv, tableToTsv } from '../../lib/csv';
import { CopyButton, IconButton } from '../ui';
import { useAdapter } from '../../adapters/context';
import { ErrorBoundary } from '../ErrorBoundary';

const VIRTUALIZE_AT = 200;
const ROW_H = 37;

type Sort = { index: number; dir: 'asc' | 'desc' } | null;

/** Column resize bounds (px). Keyboard steps 16 px, 48 px with Shift. */
export const MIN_COL = 56;
export const MAX_COL = 640;
export const clampCol = (w: number) => Math.round(Math.min(MAX_COL, Math.max(MIN_COL, w)));
const fixed = (w: number | undefined) => (w === undefined ? undefined : { width: w, minWidth: w, maxWidth: w });

/** Null, blank, and non-finite values sort after every real number (Excel-style blanks). */
function compareNumeric(a: unknown, b: unknown): number {
  const to = (v: unknown) => {
    if (typeof v === 'number') return v;
    if (typeof v === 'string' && v.trim() !== '') return Number(v);
    return Number.NaN;
  };
  const na = to(a);
  const nb = to(b);
  const fa = Number.isFinite(na);
  const fb = Number.isFinite(nb);
  if (fa && fb) return na === nb ? 0 : na > nb ? 1 : -1;
  if (!fa && !fb) return 0;
  return fa ? -1 : 1;
}

function compareCells(a: unknown, b: unknown, numeric: boolean): number {
  if (numeric) return compareNumeric(a, b);
  const aNil = a === null || a === undefined || a === '';
  const bNil = b === null || b === undefined || b === '';
  if (aNil || bNil) return aNil === bNil ? 0 : aNil ? 1 : -1;
  return String(a).localeCompare(String(b), undefined, { numeric: true });
}

type DataTableProps = { part: TablePart; onOpen?: () => void; full?: boolean };

/** A table that fails to render becomes an inline "Try again" card; the rest of the answer stays. */
/** Titles that only say "this is a result" are noise above a table (QA-A24). */
export const shownTitle = (t?: string) => (t && !/^\s*(query\s+)?results?\s*$/i.test(t) ? t : '');

export const DataTable = memo(function DataTable(props: DataTableProps) {
  return (
    <ErrorBoundary scope="section" name="DataTable" resetKey={props.part}>
      <DataTableView {...props} />
    </ErrorBoundary>
  );
});

const DataTableView = memo(function DataTableView({ part, onOpen, full }: DataTableProps) {
  const adapter = useAdapter();
  const [sort, setSort] = useState<Sort>(null);
  const [filter, setFilter] = useState('');
  // Column widths the reader dragged (index → px); unset columns size to content.
  const [widths, setWidths] = useState<Record<number, number>>({});
  // A focusable separator needs aria-valuenow (axe aria-required-attr): report the natural width until the user resizes.
  const headRef = useRef<HTMLTableSectionElement>(null);
  const [natural, setNatural] = useState<number[]>([]);
  useLayoutEffect(() => {
    const ths = headRef.current?.querySelectorAll('th');
    if (ths) setNatural(Array.from(ths, (th) => Math.round(th.getBoundingClientRect().width)));
  }, [part.columns]);
  const setWidth = (i: number, w: number | null) => setWidths((cur) => { const next = { ...cur }; if (w === null) delete next[i]; else next[i] = clampCol(w); return next; });
  const startResize = (i: number) => (e: React.PointerEvent<HTMLElement>) => {
    e.preventDefault();
    e.stopPropagation();
    const th = e.currentTarget.parentElement as HTMLElement;
    const x0 = e.clientX, w0 = th.getBoundingClientRect().width;
    const move = (ev: PointerEvent) => setWidth(i, w0 + ev.clientX - x0);
    const up = () => { window.removeEventListener('pointermove', move); window.removeEventListener('pointerup', up); document.body.classList.remove('cursor-col-resize', 'select-none'); };
    window.addEventListener('pointermove', move);
    window.addEventListener('pointerup', up);
    document.body.classList.add('cursor-col-resize', 'select-none');
  };
  const keyResize = (i: number) => (e: React.KeyboardEvent<HTMLElement>) => {
    const step = e.shiftKey ? 48 : 16;
    const cur = widths[i] ?? (e.currentTarget.parentElement as HTMLElement).getBoundingClientRect().width;
    if (e.key === 'ArrowRight') { e.preventDefault(); setWidth(i, cur + step); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); setWidth(i, cur - step); }
    else if (e.key === 'Home' || e.key === 'Escape') { e.preventDefault(); setWidth(i, null); }
  };
  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let next = part.rows;
    // Match what the user sees ("$1,234.50", "12.3%") as well as the raw value ("1234.5"); never PHI columns.
    if (q) next = part.rows.filter((row) => row.some((cell, i) => {
      const c = part.columns[i];
      if (!c || c.phi) return false;
      return (typeof cell === 'object' && cell !== null ? '' : String(cell ?? '')).toLowerCase().includes(q) || formatCell(cell, c).toLowerCase().includes(q);
    }));
    if (!sort) return next;
    const numeric = isNumeric(part.columns[sort.index]);
    const { index, dir } = sort;
    const copy = next.slice();
    copy.sort((a, b) => {
      const c = compareCells(a[index], b[index], numeric);
      return dir === 'asc' ? c : -c;
    });
    return copy;
  }, [part.rows, part.columns, filter, sort]);
  const cycle = (index: number) => {
    setSort((cur) => {
      if (!cur || cur.index !== index) return { index, dir: 'asc' };
      if (cur.dir === 'asc') return { index, dir: 'desc' };
      return null;
    });
  };
  const scrollRef = useRef<HTMLDivElement>(null);
  const virtual = rows.length > VIRTUALIZE_AT;
  const rv = useVirtualizer({
    count: virtual ? rows.length : 0,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => ROW_H,
    overscan: 12,
    // Paint a window before the scrollport is measured, so the first frame is not blank.
    initialRect: { width: 800, height: full ? 600 : 400 },
  });
  // QA-A4: fade the edge that has more columns hidden behind it, so a cut-off column reads as "scroll me".
  const [edges, setEdges] = useState({ left: false, right: false });
  const measureEdges = useCallback(() => {
    const el = scrollRef.current;
    if (!el) return;
    const left = el.scrollLeft > 1;
    const right = el.scrollLeft + el.clientWidth < el.scrollWidth - 1;
    setEdges((e) => (e.left === left && e.right === right ? e : { left, right }));
  }, []);
  useEffect(() => {
    measureEdges();
    const el = scrollRef.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(measureEdges);
    ro.observe(el);
    if (el.firstElementChild) ro.observe(el.firstElementChild);
    return () => ro.disconnect();
  }, [measureEdges, part.columns.length]);
  const items = virtual ? rv.getVirtualItems() : [];
  const padTop = virtual && items.length ? items[0].start : 0;
  const padBottom = virtual && items.length ? rv.getTotalSize() - items[items.length - 1].end : 0;
  const shown = virtual ? items.map((v) => ({ key: v.index, row: rows[v.index] })) : rows.map((row, index) => ({ key: index, row }));
  const hasPhi = part.columns.some((c) => c.phi);
  // Contract 1.11.0: a link shared without data keeps the columns and the real row count, but no rows.
  const withheld = !!part.dataWithheld;
  const rowsLabel = `${part.rowCount.toLocaleString()} ${part.rowCount === 1 ? 'row' : 'rows'}`;
  const download = async () => {
    const name = csvFilename(part.title);
    if (part.csvFileId && adapter) {
      try {
        return downloadBlob(name, await adapter.fetchBlob(part.csvFileId));
      } catch {
        /* fall back to client CSV */
      }
    }
    downloadText(name, tableToCsv(part));
  };

  return (
    // QA-A24: inline, a table sits in the column like ChatGPT's (thin header rule, hover toolbar), not in a framed
    // card with a caption row. A generic title ("Result") says nothing, so it isn't shown; a real one stays as a quiet label.
    <div className={clsx('group/table overflow-hidden', full ? 'rounded-xl border border-line bg-elev' : 'my-4')} data-testid="data-table" data-frame={full ? 'panel' : 'inline'} tabIndex={-1}>
      <div className={clsx('flex items-center justify-between gap-2', full ? 'px-3 pt-2 pb-1' : 'pb-1')}>
        <div className="min-w-0 truncate text-[13px] font-medium text-fg-muted" data-testid="table-title">{shownTitle(part.title)}</div>
        <div className={clsx('flex shrink-0 items-center gap-0.5', !full && 'opacity-0 transition-opacity group-hover/table:opacity-100 group-focus-within/table:opacity-100 [@media(hover:none)]:opacity-100')}>
          {full && (
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter rows" aria-label="Filter rows" className="mr-1 h-7 rounded-md border border-line bg-bg px-2 text-xs outline-none" />
          )}
          {!withheld && <CopyButton text={() => tableToTsv(part)} label="Copy table" className="h-7 w-7" />}
          {!withheld && (
            <IconButton label="Download CSV" className="h-7 w-7" onClick={download}>
              <Download size={15} />
            </IconButton>
          )}
          {onOpen && !withheld && (
            <IconButton label="Expand table" className="h-7 w-7" onClick={onOpen}>
              <Maximize2 size={14} />
            </IconButton>
          )}
        </div>
      </div>
      <div className="relative">
      <div
        ref={scrollRef}
        tabIndex={0}
        aria-label="Table rows"
        onScroll={measureEdges}
        className={clsx('overflow-auto overscroll-x-contain', full ? 'max-h-[calc(100vh-170px)]' : 'max-h-[400px]')}
        style={virtual ? { height: full ? 'calc(100vh - 170px)' : 400 } : undefined}
        data-virtual={virtual || undefined}
      >
        <table className="w-max min-w-full border-collapse font-sans text-sm tabular-nums">
          <thead ref={headRef} className={clsx('sticky top-0 z-[1]', full ? 'bg-elev' : 'bg-bg')}>
            <tr>
              {part.columns.map((col, i) => {
                const numeric = isNumeric(col);
                const dir = sort?.index === i ? sort.dir : null;
                return (
                  <th key={`${i}:${col.key}`} scope="col" aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'} title={col.description} style={fixed(widths[i])}
                    className={clsx('group/th relative border-b border-line-strong px-3 py-2 font-semibold whitespace-nowrap', numeric ? 'text-right' : 'text-left', widths[i] !== undefined && 'overflow-hidden text-ellipsis')}>
                    <button type="button" disabled={!!col.phi || withheld} onClick={() => cycle(i)} className={clsx('inline-flex items-center gap-1', numeric && 'flex-row-reverse')}>
                      {col.label}
                      {col.phi && <ShieldAlert size={12} className="text-fg-subtle" aria-label="PHI, masked" />}
                      {dir === 'asc' ? <ArrowUp size={13} /> : dir === 'desc' ? <ArrowDown size={13} /> : null}
                    </button>
                    {/* Drag (or ←/→, Shift for bigger steps; Home/double-click resets) to resize, like Sheets/ChatGPT tables. */}
                    <span role="separator" aria-orientation="vertical" aria-label={`Resize column ${col.label}`} tabIndex={0}
                      aria-valuenow={Math.round(widths[i] ?? natural[i] ?? MIN_COL)} aria-valuemin={MIN_COL} aria-valuemax={MAX_COL}
                      onPointerDown={startResize(i)} onKeyDown={keyResize(i)} onDoubleClick={() => setWidth(i, null)} onClick={(e) => e.stopPropagation()}
                      className="absolute inset-y-0 right-0 z-[2] w-2 cursor-col-resize touch-none select-none opacity-0 transition-opacity after:absolute after:inset-y-2 after:right-0 after:w-px after:bg-fg-subtle group-hover/th:opacity-100 focus-visible:opacity-100 focus-visible:outline-none focus-visible:after:w-0.5 focus-visible:after:bg-[var(--focus)]"
                      data-testid="col-resize" />
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {padTop > 0 && <tr aria-hidden style={{ height: padTop }} />}
            {shown.map(({ key, row }) => (
              <tr key={key} className="hover:bg-hover" style={virtual ? { height: ROW_H } : undefined}>
                {part.columns.map((col, i) => (
                  <td key={`${i}:${col.key}`} style={fixed(widths[i])} title={widths[i] !== undefined && !col.phi ? String(row[i] ?? '') : undefined}
                    className={clsx('border-b border-line px-3 py-2 whitespace-nowrap', isNumeric(col) && 'text-right tabular-nums', col.phi && 'font-mono text-fg-subtle', widths[i] !== undefined && 'overflow-hidden text-ellipsis')}>
                    {!col.phi && (row[i] === null || row[i] === undefined || row[i] === '') ? <span className="text-fg-subtle" title="No value">—</span> : formatCell(row[i], col)}
                  </td>
                ))}
              </tr>
            ))}
            {padBottom > 0 && <tr aria-hidden style={{ height: padBottom }} />}
            {rows.length === 0 && (withheld ? (
              <tr><td colSpan={part.columns.length} className="px-3 py-6 text-center text-fg-subtle" data-testid="table-withheld">
                <span className="inline-flex items-center gap-1.5"><Lock size={13} aria-hidden />Data not shared ({rowsLabel})</span>
                <span className="mt-0.5 block text-xs">The owner shared this chat without result data.</span>
              </td></tr>
            ) : (
              <tr><td colSpan={part.columns.length} className="px-3 py-6 text-center text-fg-subtle" data-testid="table-empty">
                {filter.trim() && part.rows.length > 0
                  ? <>No rows match “{filter.trim()}”. <button type="button" className="underline underline-offset-2 hover:text-fg" onClick={() => setFilter('')}>Clear filter</button></>
                  : 'Query returned 0 rows'}
              </td></tr>
            ))}
          </tbody>
        </table>
      </div>
        <div aria-hidden data-testid="table-fade-left" data-on={edges.left || undefined}
          className={clsx('pointer-events-none absolute inset-y-0 left-0 z-[2] w-6 bg-gradient-to-r to-transparent transition-opacity', full ? 'from-elev' : 'from-bg', edges.left ? 'opacity-100' : 'opacity-0')} />
        <div aria-hidden data-testid="table-fade-right" data-on={edges.right || undefined}
          className={clsx('pointer-events-none absolute inset-y-0 right-0 z-[2] w-8 bg-gradient-to-l to-transparent transition-opacity', full ? 'from-elev' : 'from-bg', edges.right ? 'opacity-100' : 'opacity-0')} />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 text-xs text-fg-subtle">
        <span>
          {part.source ? <>from <code className="font-mono">{part.source.views.join(', ')}</code> · </> : null}
          {withheld ? `${rowsLabel} · data not shared` : <>Showing {part.rows.length.toLocaleString()} of {part.rowCount.toLocaleString()} rows</>}
          {part.source?.elapsedMs !== undefined && ` · ${(part.source.elapsedMs / 1000).toFixed(2)} s`}
          {hasPhi && ' · PHI masked'}
        </span>
        {!full && onOpen && !withheld && part.rowCount > 8 && (
          <button type="button" onClick={onOpen} className="underline underline-offset-2 hover:text-fg">Open full table</button>
        )}
      </div>
    </div>
  );
});
