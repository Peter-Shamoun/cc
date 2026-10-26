import { createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import clsx from 'clsx';
import { IconButton } from '../components/ui';

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** Keep Tab inside `ref` and restore focus to whatever opened it on unmount (WAI-ARIA dialog pattern). */
export function useFocusTrap(ref: RefObject<HTMLElement | null>, active = true) {
  useEffect(() => {
    if (!active) return;
    const opener = document.activeElement as HTMLElement | null;
    const el = ref.current;
    if (el && !el.contains(document.activeElement)) (el.querySelector<HTMLElement>('[autofocus],[data-autofocus]') ?? el.querySelector<HTMLElement>(FOCUSABLE) ?? el).focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !ref.current) return;
      const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null || n === document.activeElement);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); if (opener?.isConnected) opener.focus(); };
  }, [active, ref]);
}

/** Centered modal (settings, search, shortcuts); a full-screen sheet on phones. Esc and scrim click close it. */
export function Dialog({ title, onClose, children, className, hideTitle, testId }: { title: string; onClose: () => void; children: ReactNode; className?: string; hideTitle?: boolean; testId?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  useFocusTrap(ref);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);
  return createPortal(
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-3 pt-[10vh] font-sans max-sm:p-0 max-sm:pt-0 motion-safe:animate-[cc-fade_120ms_ease-out] dark:bg-black/60" onMouseDown={(e) => e.target === e.currentTarget && onClose()} data-testid={testId ? `${testId}-scrim` : undefined}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1} data-testid={testId}
        className={clsx('relative w-full max-w-[680px] rounded-2xl border border-line bg-elev text-fg shadow-[0_16px_70px_rgba(0,0,0,0.25)] outline-none motion-safe:animate-[cc-pop_160ms_cubic-bezier(.2,.9,.3,1.2)] max-sm:flex max-sm:min-h-full max-sm:flex-col max-sm:rounded-none max-sm:border-0 max-sm:shadow-none max-sm:motion-safe:animate-[cc-rise_200ms_ease-out]', className, 'max-sm:max-w-none')}>
        <div className={clsx('flex items-center justify-between px-5 pt-4', hideTitle && 'sr-only')}>
          <h2 id={id} className="text-lg font-semibold">{title}</h2>
          <IconButton label="Close" onClick={onClose}><X size={18} /></IconButton>
        </div>
        {children}
      </div>
    </div>,
    document.body,
  );
}

export type MenuItem = { label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean; shortcut?: string } | 'sep';

/**
 * Popover menu anchored to a button (thread "…" menu, account menu, share menu).
 * Roving focus: Arrow keys/Home/End move, Enter/Space pick, Esc/Tab close and return focus.
 */
export function Menu({ anchor, items, onClose, align = 'start', side = 'bottom', label }: { anchor: HTMLElement; items: MenuItem[]; onClose: () => void; align?: 'start' | 'end'; side?: 'bottom' | 'top'; label: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  useLayoutEffect(() => {
    const r = anchor.getBoundingClientRect();
    const el = ref.current;
    const w = el?.offsetWidth ?? 200, h = el?.offsetHeight ?? 160;
    let left = align === 'start' ? r.left : r.right - w;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    let top = side === 'bottom' ? r.bottom + 4 : r.top - h - 4;
    if (top + h > window.innerHeight - 8) top = r.top - h - 4;
    if (top < 8) top = r.bottom + 4;
    setPos({ top, left });
  }, [anchor, align, side]);
  useEffect(() => { ref.current?.querySelector<HTMLElement>('[role="menuitem"]')?.focus(); }, [pos === null]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const down = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node) && !anchor.contains(e.target as Node)) onClose(); };
    document.addEventListener('mousedown', down);
    return () => document.removeEventListener('mousedown', down);
  }, [anchor, onClose]);
  const close = () => { onClose(); anchor.focus(); };
  const onKey = (e: React.KeyboardEvent) => {
    const els = [...(ref.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? [])];
    const i = els.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => { e.preventDefault(); els[(n + els.length) % els.length]?.focus(); };
    if (e.key === 'ArrowDown') go(i + 1);
    else if (e.key === 'ArrowUp') go(i - 1);
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(els.length - 1);
    else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); close(); }
  };
  return createPortal(
    <div ref={ref} role="menu" aria-label={label} onKeyDown={onKey} style={pos ?? { top: -9999, left: -9999 }}
      className="fixed z-[60] min-w-[200px] rounded-2xl border border-line bg-elev p-1.5 font-sans text-sm text-fg shadow-[0_8px_30px_rgba(0,0,0,0.16)] motion-safe:animate-[cc-pop_120ms_ease-out]">
      {items.map((it, i) =>
        it === 'sep' ? <div key={`s${i}`} role="separator" className="my-1 h-px bg-line" /> : (
          <button key={it.label} type="button" role="menuitem" tabIndex={-1} onClick={() => { onClose(); it.onSelect(); }}
            className={clsx('flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left outline-none hover:bg-hover focus-visible:bg-hover focus-visible:outline-none', it.danger && 'text-danger')}>
            {it.icon && <span className="shrink-0 opacity-80">{it.icon}</span>}
            <span className="flex-1">{it.label}</span>
            {it.shortcut && <kbd className="font-sans text-xs text-fg-subtle">{it.shortcut}</kbd>}
          </button>
        ),
      )}
    </div>,
    document.body,
  );
}

/** Hook: `[open, anchorProps, menu]` for a button that toggles a Menu. */
export function useMenu() {
  const [anchor, setAnchor] = useState<HTMLElement | null>(null);
  const toggle = useCallback((e: React.MouseEvent<HTMLElement>) => { const t = e.currentTarget; setAnchor((a) => (a ? null : t)); }, []);
  return { anchor, toggle, close: useCallback(() => setAnchor(null), []) };
}

// ---------- Toasts ----------
type Toast = { id: number; text: string; action?: { label: string; run: () => void }; onExpire?: () => void; ms: number };
const ToastCtx = createContext<(t: Omit<Toast, 'id' | 'ms'> & { ms?: number }) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

/** Bottom-centre toasts (Claude/ChatGPT "Chat deleted · Undo"). Announced through a polite live region. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const timers = useRef(new Map<number, number>());
  const dismiss = useCallback((id: number, expired: boolean) => {
    setToasts((ts) => {
      const t = ts.find((x) => x.id === id);
      if (t && expired) t.onExpire?.();
      return ts.filter((x) => x.id !== id);
    });
    window.clearTimeout(timers.current.get(id));
    timers.current.delete(id);
  }, []);
  const push = useCallback((t: Omit<Toast, 'id' | 'ms'> & { ms?: number }) => {
    const id = Date.now() + Math.random();
    const toast = { ...t, id, ms: t.ms ?? 5000 };
    setToasts((ts) => [...ts.slice(-2), toast]);
    timers.current.set(id, window.setTimeout(() => dismiss(id, true), toast.ms));
  }, [dismiss]);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 top-3 z-[70] flex flex-col items-center gap-2 px-3 font-sans" role="status" aria-live="polite" data-testid="toasts">
        {toasts.map((t) => (
          <div key={t.id} className="pointer-events-auto flex items-center gap-3 rounded-xl bg-fg px-4 py-2.5 text-sm text-bg shadow-lg motion-safe:animate-[cc-drop_180ms_ease-out]" data-testid="toast">
            <span>{t.text}</span>
            {t.action && (
              <button type="button" className="rounded-md px-2 py-0.5 font-semibold underline-offset-2 hover:underline" onClick={() => { t.action!.run(); dismiss(t.id, false); }}>{t.action.label}</button>
            )}
            <button type="button" aria-label="Dismiss" className="opacity-60 hover:opacity-100" onClick={() => dismiss(t.id, true)}><X size={14} /></button>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}

export function Kbd({ children }: { children: ReactNode }) {
  return <kbd className="inline-flex min-w-[1.5rem] items-center justify-center rounded-md border border-line bg-muted px-1.5 py-0.5 font-sans text-xs text-fg-muted">{children}</kbd>;
}

export const isMac = () => typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
export const modKey = () => (isMac() ? '⌘' : 'Ctrl');
