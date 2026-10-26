import { useEffect, useRef, useState, type ButtonHTMLAttributes, type ReactNode } from 'react';
import clsx from 'clsx';
import { Check, Copy } from 'lucide-react';
import { copyText } from '../lib/csv';

/**
 * `tip`: quick styled tooltip under the button (ChatGPT/Claude) instead of the slow native title. Pass a string to
 * show extra text such as a shortcut ("New chat · Ctrl ⇧ O"); `tipAt` moves it right of the button (rail) or
 * right-aligns it (buttons at the window's right edge).
 */
export function IconButton({ label, className, children, tip, tipAt, ...rest }: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; children: ReactNode; tip?: boolean | string; tipAt?: 'right' | 'end' }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={tip ? undefined : label}
      data-tip={tip ? (typeof tip === 'string' ? tip : label) : undefined}
      className={clsx('inline-flex h-8 w-8 items-center justify-center rounded-lg text-fg-muted hover:bg-hover hover:text-fg transition-colors disabled:opacity-40', tip && 'cc-tip', tip && tipAt && `cc-tip-${tipAt}`, className)}
      {...rest}
    >
      {children}
    </button>
  );
}

/** How long Copy shows "Copied" (ChatGPT/Claude keep the check about 2 s). */
export const COPIED_MS = 2000;

/**
 * Copy with in-place feedback, like ChatGPT/Claude: the icon becomes a check and the label "Copied" for COPIED_MS,
 * no toast. A polite live region says it for screen readers (a changed aria-label on the focused button is not
 * reliably announced). If the clipboard refuses (permissions, insecure origin), it says "Couldn't copy" instead of
 * pretending. Repeated clicks restart the timer; nothing fires after unmount.
 */
export function CopyButton({ text, label = 'Copy', withText = false, className, tip }: { text: string | (() => string); label?: string; withText?: boolean; className?: string; tip?: boolean }) {
  const [state, setState] = useState<'idle' | 'done' | 'failed'>('idle');
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => clearTimeout(timer.current), []);
  const onClick = async () => {
    const ok = await copyText(typeof text === 'function' ? text() : text);
    setState(ok ? 'done' : 'failed');
    clearTimeout(timer.current);
    timer.current = setTimeout(() => setState('idle'), COPIED_MS);
  };
  const now = state === 'done' ? 'Copied' : state === 'failed' ? "Couldn't copy" : label;
  const icon = state === 'done' ? <Check size={withText ? 14 : 16} /> : <Copy size={withText ? 14 : 16} />;
  const live = <span className="sr-only" aria-live="polite" data-testid="copy-status">{state === 'idle' ? '' : now}</span>;
  if (withText)
    return (
      <button type="button" onClick={onClick} aria-label={now} data-state={state} className={clsx('inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-fg-muted hover:bg-hover hover:text-fg', className)}>
        {icon}
        {now}
        {live}
      </button>
    );
  return (
    <>
      <IconButton label={now} onClick={onClick} className={className} tip={tip} data-state={state}>
        {icon}
      </IconButton>
      {live}
    </>
  );
}
