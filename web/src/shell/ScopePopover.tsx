import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { Check, Database, Lock, ShieldCheck } from 'lucide-react';
import clsx from 'clsx';

/** Views the demo session can read. Display only: the scope is fixed server-side (no request change). */
export const SCOPE_VIEWS = ['sem.claims', 'sem.claim_outcomes', 'sem.claim_lines', 'sem.claim_adjustments', 'sem.providers'];

/**
 * "Claims lake" scope popover above the composer chip (like ChatGPT's sources popover and Claude's
 * tools menu). It shows what a question can read. Raw EDI is listed but locked in this session.
 */
export function ScopePopover({ anchor, onClose }: { anchor: HTMLElement; onClose: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    const r = anchor.getBoundingClientRect();
    const el = ref.current;
    const w = el?.offsetWidth ?? 320, h = el?.offsetHeight ?? 260;
    const left = Math.max(8, Math.min(r.left, window.innerWidth - w - 8));
    let top = r.top - h - 8;
    if (top < 8) top = r.bottom + 8;
    setPos({ top, left });
  }, [anchor]);
  useEffect(() => { if (pos) ref.current?.querySelector<HTMLElement>('[role="radio"][aria-checked="true"]')?.focus(); }, [pos === null]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const down = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node) && !anchor.contains(e.target as Node)) onClose(); };
    document.addEventListener('mousedown', down);
    return () => document.removeEventListener('mousedown', down);
  }, [anchor, onClose]);
  const close = () => { onClose(); anchor.focus(); };
  return createPortal(
    // oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- Escape-to-close on the dialog container (APG dialog pattern)
    <div ref={ref} role="dialog" aria-labelledby={`${id}-t`} data-testid="scope-popover" style={pos ?? { top: -9999, left: -9999 }}
      onKeyDown={(e) => { if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); } }}
      className="fixed z-[60] w-[min(20rem,calc(100vw-16px))] rounded-2xl border border-line bg-elev p-1.5 font-sans text-sm text-fg shadow-[0_8px_30px_rgba(0,0,0,0.16)] motion-safe:animate-[cc-pop_120ms_ease-out]">
      <div id={`${id}-t`} className="px-2.5 pb-1 pt-1.5 text-xs font-medium text-fg-subtle">Data scope</div>
      <div role="radiogroup" aria-labelledby={`${id}-t`}>
        <div role="radio" aria-checked="true" tabIndex={0} onClick={close} onKeyDown={(e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); close(); } }}
          className="flex cursor-pointer items-start gap-2.5 rounded-lg px-2.5 py-2 outline-none hover:bg-hover focus-visible:bg-hover">
          <Database size={15} aria-hidden className="mt-0.5 shrink-0 text-fg-muted" />
          <span className="min-w-0 flex-1"><span className="block font-medium">Claims lake</span><span className="block text-[13px] text-fg-subtle">De-identified semantic views (sem.*)</span></span>
          <Check size={15} aria-hidden className="mt-0.5 shrink-0" />
        </div>
        <div role="radio" aria-checked="false" aria-disabled="true" tabIndex={-1} title="Not available in your session"
          className="flex cursor-not-allowed items-start gap-2.5 rounded-lg px-2.5 py-2 text-fg-subtle">
          <Lock size={15} aria-hidden className="mt-0.5 shrink-0" />
          <span className="min-w-0 flex-1"><span className="block font-medium">Raw EDI tables</span><span className="block text-[13px]">837/835 loops, not in your session</span></span>
        </div>
      </div>
      <hr className="my-1 h-px border-0 bg-line" />
      <div className="px-2.5 pb-1 pt-1.5 text-xs font-medium text-fg-subtle" id={`${id}-v`}>Views in scope</div>
      <ul aria-labelledby={`${id}-v`} className="flex flex-wrap gap-1 px-2.5 pb-2">
        {SCOPE_VIEWS.map((v) => <li key={v} className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-[12px] text-fg-muted">{v}</li>)}
      </ul>
      <p className={clsx('flex items-start gap-2 rounded-lg px-2.5 py-2 text-[12px] leading-4 text-fg-subtle')}>
        <ShieldCheck size={14} aria-hidden className="mt-px shrink-0" /> Read-only. PHI columns are masked for your access level.
      </p>
    </div>,
    document.body,
  );
}
