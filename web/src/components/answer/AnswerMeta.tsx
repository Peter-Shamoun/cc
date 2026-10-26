import { useEffect, useId, useRef, useState } from 'react';
import { Code2 } from 'lucide-react';
import { fmtMs, fmtTokens, fmtUsd } from './normalize';
import type { UsageDetail } from './provisional';

const BAR = ['#7c9cbf', '#10a37f', '#e8b339', '#b58cd9', '#d97757'];

/**
 * Per-answer metadata in the action row: subtle "3 queries · 12s · $0.004" (ChatGPT-style message
 * metadata). Click or focus opens a small popover with model, tokens and the time breakdown.
 */
export function AnswerMeta({ usage, queries, onSql, sqlOpen }: { usage?: UsageDetail; queries: number; onSql?: () => void; sqlOpen?: boolean }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  useEffect(() => {
    if (!open) return;
    const off = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', off);
    return () => document.removeEventListener('mousedown', off);
  }, [open]);
  if (!usage && !queries) return null;
  const bits = [
    queries > 0 && `${queries} ${queries === 1 ? 'query' : 'queries'}`,
    usage?.elapsedMs !== undefined && fmtMs(usage.elapsedMs),
    usage?.costUsd !== undefined && fmtUsd(usage.costUsd),
  ].filter(Boolean) as string[];
  const total = usage?.timing?.reduce((a, b) => a + b.ms, 0) ?? 0;
  return (
    <div ref={ref} className="relative ml-1 flex items-center gap-0.5 font-sans text-xs text-fg-subtle" data-testid="answer-meta" onKeyDown={(e) => { if (e.key === 'Escape') setOpen(false); }}>
      {onSql && queries > 0 && (
        <button type="button" onClick={onSql} aria-pressed={sqlOpen} aria-label="Show SQL" title="Show SQL" className="inline-flex h-8 items-center gap-1 rounded-lg px-2 text-fg-muted hover:bg-hover hover:text-fg" data-testid="open-sql">
          <Code2 size={15} /> <span className="hidden sm:inline">SQL</span>
        </button>
      )}
      {bits.length > 0 && (
        <button type="button" aria-expanded={open} aria-controls={id} onClick={() => setOpen((o) => !o)} className="rounded-md px-1.5 py-1 tabular-nums hover:bg-hover hover:text-fg-muted" data-testid="meta-line">
          {bits.join(' · ')}
        </button>
      )}
      {open && usage && (
        <div id={id} role="dialog" aria-label="Answer details" className="absolute bottom-full left-0 z-20 mb-2 w-72 rounded-xl border border-line bg-elev p-3 text-fg-muted shadow-lg step-enter" data-testid="meta-popover">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
            {usage.model && <><dt>Model</dt><dd className="text-right text-fg">{usage.model}</dd></>}
            {usage.inputTokens !== undefined && <><dt>Tokens</dt><dd className="text-right tabular-nums text-fg">{fmtTokens(usage.inputTokens)} in · {fmtTokens(usage.outputTokens ?? 0)} out</dd></>}
            {usage.costUsd !== undefined && <><dt>Cost</dt><dd className="text-right tabular-nums text-fg">{fmtUsd(usage.costUsd)}</dd></>}
            <dt>Queries</dt><dd className="text-right tabular-nums text-fg">{queries}{usage.repairs ? ` · ${usage.repairs} repaired` : ''}</dd>
            <dt>Total time</dt><dd className="text-right tabular-nums text-fg">{fmtMs(usage.elapsedMs)}</dd>
          </dl>
          {usage.timing && total > 0 && (
            <>
              <div className="mt-3 flex h-1.5 overflow-hidden rounded-full bg-muted" aria-hidden>
                {usage.timing.map((t, i) => <span key={t.label} style={{ width: `${(t.ms / total) * 100}%`, background: BAR[i % BAR.length] }} />)}
              </div>
              <ul className="mt-2 space-y-0.5">
                {usage.timing.map((t, i) => (
                  <li key={t.label} className="flex items-center gap-2"><span className="h-2 w-2 rounded-full" style={{ background: BAR[i % BAR.length] }} aria-hidden /><span className="flex-1">{t.label}</span><span className="tabular-nums text-fg">{fmtMs(t.ms)}</span></li>
                ))}
              </ul>
            </>
          )}
          <p className="mt-2 border-t border-line pt-2 text-[11px] leading-snug">Demo backend: costs are estimates from the scripted run, no paid model calls.</p>
        </div>
      )}
    </div>
  );
}
