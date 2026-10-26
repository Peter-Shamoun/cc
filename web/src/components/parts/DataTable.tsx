import { memo, useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { ArrowDown, ArrowUp, Download, Maximize2, ShieldAlert } from 'lucide-react';
import clsx from 'clsx';
import type { TablePart } from '../../lib/contract';
import { csvFilename, downloadBlob, downloadText, formatCell, isNumeric, tableToCsv, tableToTsv } from '../../lib/csv';
import { CopyButton, IconButton } from '../ui';
import { useAdapter } from '../../adapters/context';

const VIRTUALIZE_AT = 200;
const ROW_H = 37;

type Sort = { index: number; dir: 'asc' | 'desc' } | null;

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

export const DataTable = memo(function DataTable({ part, onOpen, full }: { part: TablePart; onOpen?: () => void; full?: boolean }) {
  const adapter = useAdapter();
  const [sort, setSort] = useState<Sort>(null);
  const [filter, setFilter] = useState('');
  const rows = useMemo(() => {
    const q = filter.trim().toLowerCase();
    let next = part.rows;
    if (q) next = part.rows.filter((row) => row.some((cell, i) => !part.columns[i]?.phi && String(cell ?? '').toLowerCase().includes(q)));
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
    <div className={clsx('group/table overflow-hidden rounded-xl border border-line bg-elev', !full && 'my-4')} data-testid="data-table" tabIndex={-1}>
      <div className="flex items-center justify-between gap-2 px-3 pt-2 pb-1">
        <div className="min-w-0 truncate text-[13px] font-medium text-fg-muted">{part.title}</div>
        <div className={clsx('flex shrink-0 items-center gap-0.5', !full && 'opacity-0 transition-opacity group-hover/table:opacity-100 group-focus-within/table:opacity-100 [@media(hover:none)]:opacity-100')}>
          {full && (
            <input value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Filter rows" aria-label="Filter rows" className="mr-1 h-7 rounded-md border border-line bg-bg px-2 text-xs outline-none" />
          )}
          <CopyButton text={() => tableToTsv(part)} label="Copy table" className="h-7 w-7" />
          <IconButton label="Download CSV" className="h-7 w-7" onClick={download}>
            <Download size={15} />
          </IconButton>
          {onOpen && (
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
        <table className="w-max min-w-full border-collapse font-sans text-sm">
          <thead className="sticky top-0 z-[1] bg-elev">
            <tr>
              {part.columns.map((col, i) => {
                const numeric = isNumeric(col);
                const dir = sort?.index === i ? sort.dir : null;
                return (
                  <th key={`${i}:${col.key}`} scope="col" aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'} title={col.description}
                    className={clsx('border-b border-line px-3 py-2 font-semibold whitespace-nowrap', numeric ? 'text-right' : 'text-left')}>
                    <button type="button" disabled={!!col.phi} onClick={() => cycle(i)} className={clsx('inline-flex items-center gap-1', numeric && 'flex-row-reverse')}>
                      {col.label}
                      {col.phi && <ShieldAlert size={12} className="text-fg-subtle" aria-label="PHI, masked" />}
                      {dir === 'asc' ? <ArrowUp size={13} /> : dir === 'desc' ? <ArrowDown size={13} /> : null}
                    </button>
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
                  <td key={`${i}:${col.key}`} className={clsx('border-b border-line px-3 py-2 whitespace-nowrap', isNumeric(col) && 'text-right tabular-nums', col.phi && 'font-mono text-fg-subtle')}>
                    {formatCell(row[i], col)}
                  </td>
                ))}
              </tr>
            ))}
            {padBottom > 0 && <tr aria-hidden style={{ height: padBottom }} />}
            {rows.length === 0 && (
              <tr><td colSpan={part.columns.length} className="px-3 py-6 text-center text-fg-subtle">Query returned 0 rows</td></tr>
            )}
          </tbody>
        </table>
      </div>
        <div aria-hidden data-testid="table-fade-left" data-on={edges.left || undefined}
          className={clsx('pointer-events-none absolute inset-y-0 left-0 z-[2] w-6 bg-gradient-to-r from-elev to-transparent transition-opacity', edges.left ? 'opacity-100' : 'opacity-0')} />
        <div aria-hidden data-testid="table-fade-right" data-on={edges.right || undefined}
          className={clsx('pointer-events-none absolute inset-y-0 right-0 z-[2] w-8 bg-gradient-to-l from-elev to-transparent transition-opacity', edges.right ? 'opacity-100' : 'opacity-0')} />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2 px-3 py-1.5 text-xs text-fg-subtle">
        <span>
          {part.source ? <>from <code className="font-mono">{part.source.views.join(', ')}</code> · </> : null}
          Showing {part.rows.length.toLocaleString()} of {part.rowCount.toLocaleString()} rows
          {part.source?.elapsedMs !== undefined && ` · ${(part.source.elapsedMs / 1000).toFixed(2)} s`}
          {hasPhi && ' · PHI masked'}
        </span>
        {!full && onOpen && part.rowCount > 8 && (
          <button type="button" onClick={onOpen} className="underline underline-offset-2 hover:text-fg">Open full table</button>
        )}
      </div>
    </div>
  );
});
