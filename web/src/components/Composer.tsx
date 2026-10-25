import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Plus, Square, Database } from 'lucide-react';
import clsx from 'clsx';

export function Composer({ onSend, onStop, busy, autoFocus, initial, placeholder = 'Ask about your claims' }: { onSend: (t: string) => void; onStop: () => void; busy: boolean; autoFocus?: boolean; initial?: { text: string; n: number }; placeholder?: string }) {
  const [value, setValue] = useState('');
  const ref = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (initial !== undefined) {
      setValue(initial.text);
      ref.current?.focus();
    }
  }, [initial]);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 200)}px`;
  }, [value]);
  const submit = () => {
    if (!value.trim() || busy) return;
    onSend(value);
    setValue('');
  };
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        submit();
      }}
      className="w-full rounded-[28px] bg-elev p-2.5 font-sans focus-within:shadow-[0_0_0_1px_var(--border-strong),0_4px_16px_rgba(0,0,0,0.06)] shadow-[var(--shadow-composer)]"
      data-testid="composer"
    >
      <textarea
        ref={ref}
        rows={1}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            submit();
          }
        }}
        placeholder={placeholder}
        aria-label="Message"
        className="block max-h-[200px] w-full resize-none bg-transparent px-3 pt-1.5 pb-2 text-base outline-none focus-visible:outline-none placeholder:text-fg-subtle"
      />
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-1">
          <button type="button" aria-label="Add files" title="Attachments (not in v0)" className="flex h-9 w-9 items-center justify-center rounded-full text-fg-muted hover:bg-hover">
            <Plus size={20} />
          </button>
          <span className="inline-flex items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs text-fg-muted" title="Queries run in a scoped, PHI-safe session">
            <Database size={13} /> Claims lake · sem.*
          </span>
        </div>
        {busy ? (
          <button type="button" onClick={onStop} aria-label="Stop generating" className="flex h-9 w-9 items-center justify-center rounded-full bg-accent text-accent-fg">
            <Square size={12} fill="currentColor" />
          </button>
        ) : (
          <button
            type="submit"
            aria-label="Send message"
            disabled={!value.trim()}
            className={clsx('flex h-9 w-9 items-center justify-center rounded-full transition-colors', value.trim() ? 'bg-accent text-accent-fg' : 'bg-accent text-accent-fg opacity-40')}
          >
            <ArrowUp size={18} strokeWidth={2.5} />
          </button>
        )}
      </div>
    </form>
  );
}
