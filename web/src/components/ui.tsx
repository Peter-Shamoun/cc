import { useState, type ButtonHTMLAttributes, type ReactNode } from 'react';
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

export function CopyButton({ text, label = 'Copy', withText = false, className, tip }: { text: string | (() => string); label?: string; withText?: boolean; className?: string; tip?: boolean }) {
  const [done, setDone] = useState(false);
  const onClick = async () => {
    if (await copyText(typeof text === 'function' ? text() : text)) {
      setDone(true);
      setTimeout(() => setDone(false), 1500);
    }
  };
  if (withText)
    return (
      <button type="button" onClick={onClick} aria-label={label} className={clsx('inline-flex items-center gap-1 rounded-md px-2 py-1 text-xs text-fg-muted hover:bg-hover hover:text-fg', className)}>
        {done ? <Check size={14} /> : <Copy size={14} />}
        {done ? 'Copied' : label}
      </button>
    );
  return (
    <IconButton label={done ? 'Copied' : label} onClick={onClick} className={className} tip={tip}>
      {done ? <Check size={16} /> : <Copy size={16} />}
    </IconButton>
  );
}
