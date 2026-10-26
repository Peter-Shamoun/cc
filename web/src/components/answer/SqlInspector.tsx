import { useEffect, useId, useRef, useState } from 'react';
import { ChevronDown, ChevronRight, Code2, Table2 } from 'lucide-react';
import clsx from 'clsx';
import type { CodePart, TablePart } from '../../lib/contract';
import { CodeBlock } from '../parts/CodeBlock';
import { fmtMs, partsOf, stepsOf, type AnyView } from './normalize';
import type { Step } from './provisional';

export interface QueryInfo { id: string; label: string; sql: string; rowCount?: number; durationMs?: number; state?: Step['state']; columns?: string[]; error?: string; produces: string[] }

/**
 * Every SQL statement behind an answer, and which table each produced. Sources: tool steps with
 * SQL (in run order) and `code` parts with role=query; a table links to its query by `sqlPartId`
 * (code part or tool step id), by v1 `stepId`, or by `resultId`.
 */
type LinkedTable = TablePart & { stepId?: string };
type LinkedCode = CodePart & { stepId?: string };
export function queriesOf(m: AnyView): QueryInfo[] {
  const parts = partsOf(m) as unknown as (LinkedTable | LinkedCode | { type: string })[];
  const tables = parts.filter((p): p is LinkedTable => p.type === 'table');
  const codes = parts.filter((p): p is LinkedCode => p.type === 'code' && (p as CodePart).language === 'sql' && (p as CodePart).role === 'query');
  const producedBy = (ids: (string | undefined)[]) => tables.filter((t) => ids.some((id) => id && (t.sqlPartId === id || t.resultId === id || t.stepId === id || codes.some((c) => c.stepId === id && c.id === t.sqlPartId)))).map((t) => t.title ?? 'Result');
  const out: QueryInfo[] = [];
  const steps = stepsOf(m).filter((s) => s.sql && s.state !== 'running');
  steps.forEach((s, i) => {
    // A v1 repair carries the revised SQL that then re-runs as its own step: list it once.
    if (s.kind === 'repair' && steps.slice(i + 1).some((x) => x.sql?.trim() === s.sql!.trim())) return;
    const dup = out.find((q) => q.sql.trim() === s.sql!.trim());
    const prod = producedBy([s.id, s.resultId]);
    if (dup) { dup.produces = [...new Set([...dup.produces, ...prod])]; return; }
    out.push({ id: s.id, label: s.label, sql: s.sql!, rowCount: s.rowCount, durationMs: s.durationMs, state: s.state, columns: s.columns, error: s.kind === 'repair' ? undefined : s.error, produces: prod });
  });
  for (const c of codes) {
    const prod = producedBy([c.id]);
    const dup = out.find((q) => q.sql.trim() === c.code.trim());
    if (dup) { dup.produces = [...new Set([...dup.produces, ...prod])]; continue; }
    out.push({ id: c.id, label: c.title ?? 'Query', sql: c.code, produces: prod, state: 'ok' });
  }
  return out;
}

/** The query that produced `t`, if known. */
export function queryForTable(m: AnyView, t: TablePart): QueryInfo | undefined {
  const qs = queriesOf(m);
  const sid = (t as LinkedTable).stepId;
  const code = (partsOf(m) as unknown as LinkedCode[]).find((c) => c.type === 'code' && c.id === t.sqlPartId);
  const ids = [t.sqlPartId, t.resultId, sid, code?.stepId].filter(Boolean);
  return qs.find((q) => ids.includes(q.id)) ?? qs.find((q) => q.produces.includes(t.title ?? 'Result'));
}

/** Inline "Query ▸" disclosure under a table, ChatGPT "View analysis" style. */
export function TableSql({ query }: { query: QueryInfo }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="-mt-1 mb-3 font-sans text-xs" data-testid="table-sql">
      <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1 rounded-md py-0.5 text-fg-subtle hover:text-fg">
        <Code2 size={13} aria-hidden /> View SQL {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
      </button>
      <div id={id} hidden={!open}>{open && <CodeBlock code={query.sql} language="sql" title={query.label} />}</div>
    </div>
  );
}

/** Answer-level inspector: tabs per query, with what each produced. Opened from the footer. */
export function SqlInspector({ queries, onClose }: { queries: QueryInfo[]; onClose: () => void }) {
  const [i, setI] = useState(() => Math.max(0, queries.findIndex((q) => q.produces.length > 0)));
  const q = queries[Math.min(i, queries.length - 1)];
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { ref.current?.scrollIntoView?.({ block: 'nearest', behavior: 'smooth' }); }, []);
  if (!q) return null;
  return ( // oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- Escape delegated from focused children
    <div ref={ref} className="mt-2 rounded-xl border border-line bg-elev p-3 font-sans text-sm step-enter" role="region" aria-label="SQL inspector" data-testid="sql-inspector"
      onKeyDown={(e) => { if (e.key === 'Escape') onClose(); }}>
      <div className="flex items-center justify-between gap-2">
        <div role="tablist" aria-label="Queries" className="flex min-w-0 gap-1 overflow-x-auto">
          {queries.map((x, k) => (
            <button key={x.id} role="tab" type="button" aria-selected={k === i} onClick={() => setI(k)}
              onKeyDown={(e) => { if (e.key === 'ArrowRight') setI((k + 1) % queries.length); if (e.key === 'ArrowLeft') setI((k - 1 + queries.length) % queries.length); }}
              className={clsx('max-w-[16rem] shrink-0 truncate rounded-full px-3 py-1 text-xs', k === i ? 'bg-fg text-bg' : 'text-fg-muted hover:bg-hover', x.state === 'error' && k !== i && 'text-danger')}>
              {k + 1}. {x.state === 'error' ? 'Failed' : x.produces.length ? x.produces[0] : x.label}
            </button>
          ))}
        </div>
        <button type="button" onClick={onClose} className="shrink-0 rounded-md px-2 py-1 text-xs text-fg-muted hover:bg-hover">Close</button>
      </div>
      <div role="tabpanel" className="mt-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-fg-subtle">
          <span className="text-fg-muted">{q.label}</span>
          {q.rowCount !== undefined && <span>{q.rowCount.toLocaleString()} rows</span>}
          {q.durationMs !== undefined && <span>{fmtMs(q.durationMs)}</span>}
          {q.state === 'error' && <span className="text-danger">failed, repaired below</span>}
          {q.produces.map((t) => <span key={t} className="inline-flex items-center gap-1"><Table2 size={12} aria-hidden /> produced “{t}”</span>)}
        </div>
        {q.error && <div className="mt-2 rounded-lg border border-danger/25 bg-danger/5 px-3 py-1.5 font-mono text-xs text-fg" data-testid="inspector-error">{q.error}</div>}
        <CodeBlock code={q.sql} language="sql" title={`query ${Math.min(i, queries.length - 1) + 1} of ${queries.length}`} />
        {q.columns && q.columns.length > 0 && <div className="mt-1 text-xs text-fg-subtle" data-testid="inspector-columns">Columns: {q.columns.map((c) => <code key={c} className="mr-1.5 font-mono">{c}</code>)}</div>}
      </div>
    </div>
  );
}
