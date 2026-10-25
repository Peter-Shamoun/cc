import { useMemo, useState } from 'react';
import { flexRender, getCoreRowModel, getFilteredRowModel, getSortedRowModel, useReactTable, type ColumnDef, type SortingState } from '@tanstack/react-table';
import { ArrowDown, ArrowUp, Download, Maximize2, ShieldAlert } from 'lucide-react';
import clsx from 'clsx';
import type { TablePart } from '../../lib/contract';
import { csvFilename, downloadBlob, downloadText, formatCell, isNumeric, tableToCsv, tableToTsv } from '../../lib/csv';
import { CopyButton, IconButton } from '../ui';
import { useAdapter } from '../../adapters/context';

type Row = unknown[];

export function DataTable({ part, onOpen, full }: { part: TablePart; onOpen?: () => void; full?: boolean }) {
  const adapter = useAdapter();
  const [sorting, setSorting] = useState<SortingState>([]);
  const [filter, setFilter] = useState('');
  const columns = useMemo<ColumnDef<Row>[]>(
    () =>
      part.columns.map((c, i) => ({
        id: c.key,
        accessorFn: (r) => (c.phi ? null : r[i]),
        header: c.label,
        cell: (info) => formatCell(info.row.original[i], c),
        enableSorting: !c.phi,
        sortingFn: isNumeric(c) ? 'basic' : 'alphanumeric',
        meta: { numeric: isNumeric(c), phi: c.phi, description: c.description },
      })),
    [part.columns],
  );
  const table = useReactTable({
    data: part.rows,
    columns,
    state: { sorting, globalFilter: filter },
    onSortingChange: setSorting,
    onGlobalFilterChange: setFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
  });
  const rows = table.getRowModel().rows;
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
        <div className={clsx('flex shrink-0 items-center gap-0.5', !full && 'opacity-0 transition-opacity group-hover/table:opacity-100 group-focus-within/table:opacity-100')}>
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
      <div className={clsx('overflow-auto', !full && 'max-h-[400px]')}>
        <table className="w-full border-collapse font-sans text-sm">
          <thead className="sticky top-0 z-[1] bg-elev">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => {
                  const meta = h.column.columnDef.meta as { numeric?: boolean; phi?: boolean; description?: string };
                  const dir = h.column.getIsSorted();
                  return (
                    <th key={h.id} scope="col" aria-sort={dir === 'asc' ? 'ascending' : dir === 'desc' ? 'descending' : 'none'} title={meta.description}
                      className={clsx('border-b border-line px-3 py-2 font-semibold whitespace-nowrap', meta.numeric ? 'text-right' : 'text-left')}>
                      <button type="button" disabled={!h.column.getCanSort()} onClick={h.column.getToggleSortingHandler()} className={clsx('inline-flex items-center gap-1', meta.numeric && 'flex-row-reverse')}>
                        {flexRender(h.column.columnDef.header, h.getContext())}
                        {meta.phi && <ShieldAlert size={12} className="text-fg-subtle" aria-label="PHI, masked" />}
                        {dir === 'asc' ? <ArrowUp size={13} /> : dir === 'desc' ? <ArrowDown size={13} /> : null}
                      </button>
                    </th>
                  );
                })}
              </tr>
            ))}
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className="hover:bg-hover">
                {r.getVisibleCells().map((c) => {
                  const meta = c.column.columnDef.meta as { numeric?: boolean; phi?: boolean };
                  return (
                    <td key={c.id} className={clsx('border-b border-line px-3 py-2 whitespace-nowrap', meta.numeric && 'text-right tabular-nums', meta.phi && 'font-mono text-fg-subtle')}>
                      {flexRender(c.column.columnDef.cell, c.getContext())}
                    </td>
                  );
                })}
              </tr>
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={part.columns.length} className="px-3 py-6 text-center text-fg-subtle">Query returned 0 rows</td></tr>
            )}
          </tbody>
        </table>
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
}
