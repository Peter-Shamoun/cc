import { createContext, useCallback, useContext, useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode, type RefObject } from 'react';
import { createPortal } from 'react-dom';
import { Check, X } from 'lucide-react';
import clsx from 'clsx';
import { IconButton } from '../components/ui';

const FOCUSABLE = 'a[href],button:not([disabled]),input:not([disabled]),textarea:not([disabled]),select:not([disabled]),[tabindex]:not([tabindex="-1"])';

/** Keep Tab inside `ref` and restore focus to whatever opened it on unmount (WAI-ARIA dialog pattern). */
/** `initial: 'container'` focuses the dialog itself when nothing inside asks for focus (no stray ring on a Close button). */
export function useFocusTrap(ref: RefObject<HTMLElement | null>, active = true, initial: 'first' | 'container' = 'first', openedFrom?: Element | null) {
  useEffect(() => {
    if (!active) return;
    const opener = (openedFrom ?? document.activeElement) as HTMLElement | null;
    const el = ref.current;
    if (el && !el.contains(document.activeElement)) (el.querySelector<HTMLElement>('[autofocus],[data-autofocus]') ?? (initial === 'first' ? el.querySelector<HTMLElement>(FOCUSABLE) : null) ?? el).focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== 'Tab' || !ref.current) return;
      const items = [...ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((n) => n.offsetParent !== null || n === document.activeElement);
      if (!items.length) return;
      const first = items[0], last = items[items.length - 1];
      if (document.activeElement === ref.current) { e.preventDefault(); (e.shiftKey ? last : first).focus(); }
      else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
      else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
    };
    document.addEventListener('keydown', onKey);
    return () => { document.removeEventListener('keydown', onKey); restoreFocus(opener); };
  }, [active, ref, initial, openedFrom]);
}

/**
 * Give focus back on close: to the opener if it is still on the page, else to the app's main input
 * (`data-focus-fallback`, the composer) so focus never drops to <body> (e.g. Settings opened from a menu item).
 * Phones skip the fallback so closing a sheet doesn't pop the keyboard.
 */
export function restoreFocus(opener: HTMLElement | null) {
  if (opener?.isConnected && opener !== document.body) { opener.focus(); return; }
  if (window.matchMedia?.('(pointer: coarse)').matches) return;
  document.querySelector<HTMLElement>('[data-focus-fallback]')?.focus();
}

/** Centered modal (settings, search, shortcuts); a full-screen sheet on phones. Esc and scrim click close it. */
export function Dialog({ title, onClose, children, className, hideTitle, testId }: { title: string; onClose: () => void; children: ReactNode; className?: string; hideTitle?: boolean; testId?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const id = useId();
  // Read the opener during render: a child input's autoFocus runs at commit, before any effect could see it.
  const [openedFrom] = useState(() => document.activeElement);
  useFocusTrap(ref, true, 'container', openedFrom);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); onClose(); } };
    window.addEventListener('keydown', onKey, true);
    return () => window.removeEventListener('keydown', onKey, true);
  }, [onClose]);
  return createPortal( // oxlint-disable-next-line jsx-a11y/no-static-element-interactions -- backdrop click; Escape is the keyboard path
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/40 p-3 pt-[10vh] font-sans max-sm:p-0 max-sm:pt-0 motion-safe:animate-[cc-fade_120ms_ease-out] dark:bg-black/60" onMouseDown={(e) => e.target === e.currentTarget && onClose()} data-testid={testId ? `${testId}-scrim` : undefined}>
      <div ref={ref} role="dialog" aria-modal="true" aria-labelledby={id} tabIndex={-1} data-testid={testId}
        className={clsx(!className?.includes('max-w-') && 'max-w-[680px]', 'relative w-full rounded-2xl border border-line bg-elev text-fg shadow-[var(--shadow-dialog)] outline-none motion-safe:animate-[cc-pop_150ms_var(--ease-out)] max-sm:flex max-sm:min-h-full max-sm:flex-col max-sm:rounded-none max-sm:border-0 max-sm:shadow-none max-sm:motion-safe:animate-[cc-rise_200ms_ease-out]', className, 'max-sm:max-w-none')}>
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

export type MenuItem =
  | { label: string; icon?: ReactNode; onSelect: () => void; danger?: boolean; shortcut?: string;
      /** Second line under the label (picker rows). */ description?: string;
      /** Right-aligned quiet note, e.g. "Low cost · Fast". */ meta?: string;
      /** Set for radio rows (menuitemradio + aria-checked). */ checked?: boolean; disabled?: boolean;
      /** Tooltip, e.g. why a disabled row is unavailable. */ title?: string }
  | { heading: string }
  | 'sep';
const ITEM_SEL = '[role="menuitem"]:not([aria-disabled="true"]),[role="menuitemradio"]:not([aria-disabled="true"])';

/**
 * Vertical placement for a menu of height `h` next to anchor rect `r`: the preferred side if it fits, else the
 * other side if that fits, else the roomier side with a max height so every item stays reachable (the menu
 * scrolls) instead of running off-screen.
 */
export function menuTop(r: { top: number; bottom: number }, h: number, side: 'bottom' | 'top', vh: number): { top: number; maxHeight?: number } {
  const gap = 4, edge = 8;
  const below = vh - r.bottom - gap - edge, above = r.top - gap - edge;
  const atBottom = { top: r.bottom + gap }, atTop = (hh: number) => ({ top: r.top - gap - hh });
  if (side === 'bottom' ? h <= below : h > above && h <= below) return atBottom;
  if (h <= above) return atTop(h);
  return below >= above ? { ...atBottom, maxHeight: Math.max(0, below) } : { ...atTop(above), maxHeight: Math.max(0, above) };
}

/**
 * Popover menu anchored to a button (thread "…" menu, account menu, share menu).
 * Roving focus: Arrow keys/Home/End move, Enter/Space pick, Esc/Tab close and return focus.
 */
export function Menu({ anchor, items, onClose, align = 'start', side = 'bottom', label, className }: { anchor: HTMLElement; items: MenuItem[]; onClose: () => void; align?: 'start' | 'end'; side?: 'bottom' | 'top'; label: string; className?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [pos, setPos] = useState<{ top: number; left: number; maxHeight?: number } | null>(null);
  useLayoutEffect(() => {
    const r = anchor.getBoundingClientRect();
    const el = ref.current;
    const w = el?.offsetWidth ?? 200, h = el?.scrollHeight || el?.offsetHeight || 160;
    let left = align === 'start' ? r.left : r.right - w;
    left = Math.max(8, Math.min(left, window.innerWidth - w - 8));
    setPos({ left, ...menuTop(r, h, side, window.innerHeight) });
  }, [anchor, align, side]);
  useEffect(() => { (ref.current?.querySelector<HTMLElement>('[aria-checked="true"]') ?? ref.current?.querySelector<HTMLElement>(ITEM_SEL))?.focus(); }, [pos === null]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const down = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node) && !anchor.contains(e.target as Node)) onClose(); };
    document.addEventListener('mousedown', down);
    return () => document.removeEventListener('mousedown', down);
  }, [anchor, onClose]);
  const close = () => { onClose(); anchor.focus(); };
  const onKey = (e: React.KeyboardEvent) => {
    const els = [...(ref.current?.querySelectorAll<HTMLElement>(ITEM_SEL) ?? [])];
    const i = els.indexOf(document.activeElement as HTMLElement);
    const go = (n: number) => { e.preventDefault(); els[(n + els.length) % els.length]?.focus(); };
    if (e.key === 'ArrowDown') go(i + 1);
    else if (e.key === 'ArrowUp') go(i - 1);
    else if (e.key === 'Home') go(0);
    else if (e.key === 'End') go(els.length - 1);
    else if (e.key === 'Escape' || e.key === 'Tab') { e.preventDefault(); e.stopPropagation(); close(); }
  };
  return createPortal( // oxlint-disable-next-line jsx-a11y/interactive-supports-focus -- arrow keys delegated from focused menuitems (roving tabindex)
    <div ref={ref} role="menu" aria-label={label} onKeyDown={onKey} style={pos ?? { top: -9999, left: -9999 }}
      className={clsx(className, "scrollbar-thin fixed z-[60] min-w-[200px] overflow-y-auto overscroll-contain rounded-2xl border border-line bg-elev p-1.5 font-sans text-sm text-fg shadow-[var(--shadow-menu)] motion-safe:animate-[cc-pop_120ms_ease-out]")}>
      {items.map((it, i) => // oxlint-disable-next-line jsx-a11y/control-has-associated-label -- non-focusable menu separator needs no label
        it === 'sep' ? <div key={`s${i}`} role="separator" className="my-1 h-px bg-line" /> : 'heading' in it ? (
          <div key={`h${i}`} role="presentation" className="px-2.5 pb-1 pt-2 text-xs font-medium text-fg-subtle">{it.heading}</div>
        ) : (
          <button key={it.label} type="button" role={it.checked === undefined ? 'menuitem' : 'menuitemradio'} aria-checked={it.checked} aria-disabled={it.disabled || undefined} tabIndex={-1}
            onClick={() => { if (it.disabled) return; onClose(); it.onSelect(); }}
            title={it.title}
            className={clsx('flex w-full gap-2.5 rounded-lg px-2.5 text-left outline-none focus-visible:outline-none', it.description ? 'items-start py-1.5' : 'items-center py-2', it.disabled ? 'cursor-not-allowed text-fg-subtle' : 'hover:bg-hover focus-visible:bg-hover', it.danger && 'text-danger')}>
            {it.icon && <span className={clsx('shrink-0 opacity-80', it.description && 'mt-0.5')}>{it.icon}</span>}
            <span className="min-w-0 flex-1">
              <span className="flex items-baseline gap-3">
                <span className="min-w-0 truncate">{it.label}</span>
                {it.meta && <span className="ml-auto shrink-0 whitespace-nowrap text-xs text-fg-subtle">{it.meta}</span>}
              </span>
              {it.description && <span className="block text-[13px] leading-[18px] text-fg-subtle" data-testid="menu-desc">{it.description}</span>}
            </span>
            {it.shortcut && <kbd className="font-sans text-xs text-fg-subtle">{it.shortcut}</kbd>}
            {it.checked !== undefined && <Check size={16} aria-hidden className={clsx('shrink-0', it.description && 'mt-0.5', !it.checked && 'invisible')} />}
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
type Toast = { id: number; text: string; action?: { label: string; run: () => void }; onExpire?: () => void; ms: number; leaving?: boolean };
/** Exit animation length; 0 when the user prefers reduced motion (the toast just goes). */
const TOAST_EXIT_MS = 160;
const reducedMotion = () => typeof window.matchMedia !== 'function' || window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const ToastCtx = createContext<(t: Omit<Toast, 'id' | 'ms'> & { ms?: number }) => void>(() => {});
export const useToast = () => useContext(ToastCtx);

/** Top-centre toasts (Claude/ChatGPT "Chat deleted · Undo"). Announced through a polite live region. */
export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  // Each timer remembers its deadline so hovering or focusing the stack can pause it (WCAG 2.2.1: an Undo
  // toast must not vanish while someone is reading it or reaching for the button).
  const timers = useRef(new Map<number, { handle?: number; left: number; since: number }>());
  const paused = useRef(false);
  const toastsRef = useRef(toasts);
  useLayoutEffect(() => { toastsRef.current = toasts; });
  const resumeRef = useRef(() => {});
  const dismiss = useCallback((id: number, expired: boolean) => {
    const t = toastsRef.current.find((x) => x.id === id);
    if (!t || t.leaving) return;
    if (expired) t.onExpire?.();
    const remove = () => setToasts((ts) => ts.filter((x) => x.id !== id));
    if (reducedMotion()) remove();
    else { setToasts((ts) => ts.map((x) => (x.id === id ? { ...x, leaving: true } : x))); window.setTimeout(remove, TOAST_EXIT_MS); }
    window.clearTimeout(timers.current.get(id)?.handle);
    timers.current.delete(id);
    // The hovered toast can vanish (Undo/Dismiss clicked) without a mouseleave: don't leave the rest frozen.
    if (paused.current) queueMicrotask(() => resumeRef.current());
  }, []);
  const arm = useCallback((id: number) => {
    const t = timers.current.get(id);
    if (!t) return;
    t.since = Date.now();
    t.handle = window.setTimeout(() => dismiss(id, true), t.left);
  }, [dismiss]);
  const push = useCallback((t: Omit<Toast, 'id' | 'ms'> & { ms?: number }) => {
    const id = Date.now() + Math.random();
    const toast = { ...t, id, ms: t.ms ?? 5000 };
    setToasts((ts) => [...ts.slice(-2), toast]);
    timers.current.set(id, { left: toast.ms, since: Date.now() });
    if (!paused.current) arm(id);
  }, [arm]);
  const pause = () => {
    if (paused.current) return;
    paused.current = true;
    for (const t of timers.current.values()) { window.clearTimeout(t.handle); t.handle = undefined; t.left = Math.max(0, t.left - (Date.now() - t.since)); }
  };
  const resume = () => {
    if (!paused.current) return;
    paused.current = false;
    // Give at least a beat after the pointer leaves, so an expiring Undo doesn't disappear under the cursor.
    // oxlint-disable-next-line react/immutability -- timer records live in a ref'd Map, mutated only from events
    for (const [id, t] of timers.current) { t.left = Math.max(t.left, 1500); arm(id); }
  };
  useLayoutEffect(() => { resumeRef.current = resume; });
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 top-3 z-[70] flex flex-col items-center gap-2 px-3 font-sans" role="status" aria-live="polite" data-testid="toasts">
        {toasts.map((t) => (
          <div key={t.id} aria-hidden={t.leaving || undefined} data-leaving={t.leaving || undefined}
            className={clsx('flex items-center gap-3 rounded-xl bg-fg px-4 py-2.5 text-sm text-bg shadow-lg', t.leaving ? 'pointer-events-none motion-safe:animate-[cc-lift_160ms_ease-in_forwards]' : 'pointer-events-auto motion-safe:animate-[cc-drop_180ms_ease-out]')} data-testid="toast"
            onMouseEnter={pause} onMouseLeave={resume} onFocus={pause} onBlur={(e) => { if (!e.currentTarget.parentElement?.contains(e.relatedTarget as Node)) resume(); }}>
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
