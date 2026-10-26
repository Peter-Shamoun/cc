import { useEffect, useId, useRef, useState } from 'react';
import { ArrowUp, Plus, Square, Database, X } from 'lucide-react';
import clsx from 'clsx';
import { getDraft, setDraft } from '../shell/drafts';
import { matchSlash, type SlashCommand } from '../shell/slash';

/** Pastes this long become an attachment chip instead of flooding the box (Claude-style). */
export const PASTE_CHARS = 1200;
export const PASTE_LINES = 15;
export const isLongPaste = (t: string) => t.length >= PASTE_CHARS || t.split('\n').length > PASTE_LINES;

export interface ComposerProps {
  onSend: (t: string) => void;
  onStop: () => void;
  busy: boolean;
  autoFocus?: boolean;
  /** Text pushed in from outside (edit-from-answer, suggestion); `n` bumps to re-apply the same text. */
  initial?: { text: string; n: number };
  placeholder?: string;
  /** Per-chat draft key; drafts live in memory only (see shell/drafts.ts). */
  draftKey?: string;
  enterToSend?: boolean;
  onCommand?: (c: SlashCommand) => void;
  /** Up-arrow in an empty composer: edit the last question. Return false if there is none. */
  onEditLast?: () => boolean;
}

/**
 * ChatGPT/Claude-style composer: auto-growing textarea, send button that morphs into stop while a
 * reply streams, Enter vs Shift+Enter (configurable), IME-safe, per-chat drafts and a "/" command menu.
 */
export function Composer({ onSend, onStop, busy, autoFocus, initial, placeholder = 'Ask about your claims', draftKey, enterToSend = true, onCommand, onEditLast }: ComposerProps) {
  const [value, setValueState] = useState(() => (draftKey ? getDraft(draftKey) : ''));
  const ref = useRef<HTMLTextAreaElement>(null);
  const [slashIdx, setSlashIdx] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const lid = useId();
  // Long pastes ride along as chips; they live in component memory only (never drafts or storage).
  const [pastes, setPastes] = useState<{ id: number; text: string }[]>([]);
  const pasteSeq = useRef(0);
  const setValue = (t: string) => { setValueState(t); if (draftKey) setDraft(draftKey, t); };
  useEffect(() => { setValueState(draftKey ? getDraft(draftKey) : ''); setPastes([]); }, [draftKey]);
  useEffect(() => {
    if (initial !== undefined) {
      setValue(initial.text);
      const el = ref.current;
      el?.focus();
      requestAnimationFrame(() => el?.setSelectionRange(el.value.length, el.value.length));
    }
  }, [initial]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = `${Math.min(el.scrollHeight, 240)}px`;
  }, [value]);
  const slash = slashDismissed ? null : matchSlash(value);
  useEffect(() => { setSlashIdx(0); if (!value.startsWith('/')) setSlashDismissed(false); }, [value]);
  const runCommand = (c: SlashCommand) => {
    if (c.insert) { setValue(c.insert); ref.current?.focus(); }
    else { setValue(''); onCommand?.(c); }
  };
  const submit = () => {
    if (slash?.length) { runCommand(slash[slashIdx]); return; }
    if ((!value.trim() && !pastes.length) || busy) return;
    onSend([value.trim(), ...pastes.map((x) => x.text)].filter(Boolean).join('\n\n'));
    setValue('');
    setPastes([]);
  };
  const canSend = !!value.trim() || pastes.length > 0;
  const removePaste = (id: number) => { setPastes((ps) => ps.filter((x) => x.id !== id)); ref.current?.focus(); };
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); submit(); }}
      className="relative w-full cursor-text rounded-[28px] bg-elev p-2.5 font-sans shadow-[var(--shadow-composer)] transition-shadow duration-150 focus-within:shadow-[0_0_0_2px_var(--focus),0_6px_20px_rgba(0,0,0,0.08)]"
      onClick={(e) => { if (e.target === e.currentTarget) ref.current?.focus(); }}
      data-testid="composer"
    >
      {slash && slash.length > 0 && (
        <ul id={lid} role="listbox" aria-label="Commands" className="absolute inset-x-2 bottom-full z-20 mb-2 max-h-72 overflow-y-auto rounded-2xl border border-line bg-elev p-1.5 text-sm shadow-[0_8px_30px_rgba(0,0,0,0.14)] motion-safe:animate-[cc-rise_120ms_ease-out]" data-testid="slash-menu">
          {slash.map((c, i) => (
            <li key={c.name} id={`${lid}-${i}`} role="option" aria-selected={i === slashIdx} onMouseDown={(e) => { e.preventDefault(); runCommand(c); }} onMouseMove={() => setSlashIdx(i)}
              className={clsx('flex cursor-pointer items-baseline gap-3 rounded-lg px-3 py-2', i === slashIdx && 'bg-hover')}>
              <span className="font-medium">/{c.name}</span>
              <span className="truncate text-fg-muted">{c.description}</span>
            </li>
          ))}
        </ul>
      )}
      {pastes.length > 0 && (
        <ul className="flex gap-2 overflow-x-auto px-1 pb-2 pt-1 scrollbar-none" aria-label="Pasted text" data-testid="paste-chips">
          {pastes.map((x) => {
            const lines = x.text.split('\n').length;
            return (
              <li key={x.id} className="group/chip relative flex h-[88px] w-[132px] shrink-0 flex-col justify-between overflow-hidden rounded-xl border border-line bg-bg p-2.5 text-left motion-safe:animate-[cc-pop_150ms_ease-out]" data-testid="paste-chip">
                <p className="line-clamp-3 break-all font-mono text-[10.5px] leading-[14px] text-fg-muted" aria-hidden>{x.text.slice(0, 160)}</p>
                <span className="mt-1 inline-flex w-fit rounded border border-line px-1 text-[10px] font-semibold uppercase tracking-wide text-fg-subtle">Pasted</span>
                <span className="sr-only">Pasted text, {lines} lines, {x.text.length} characters</span>
                <button type="button" onClick={() => removePaste(x.id)} aria-label={`Remove pasted text (${lines} lines)`}
                  className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full border border-line bg-elev text-fg-muted opacity-0 shadow-sm hover:text-fg focus-visible:opacity-100 group-hover/chip:opacity-100 max-md:opacity-100">
                  <X size={12} strokeWidth={2.5} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      <textarea
        onPaste={(e) => {
          const t = e.clipboardData.getData('text/plain');
          if (!isLongPaste(t)) return;
          e.preventDefault();
          setPastes((ps) => [...ps, { id: ++pasteSeq.current, text: t }]);
        }}
        ref={ref}
        rows={1}
        value={value}
        autoFocus={autoFocus}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (slash?.length) {
            if (e.key === 'ArrowDown') { e.preventDefault(); setSlashIdx((i) => (i + 1) % slash.length); return; }
            if (e.key === 'ArrowUp') { e.preventDefault(); setSlashIdx((i) => (i - 1 + slash.length) % slash.length); return; }
            if (e.key === 'Tab') { e.preventDefault(); runCommand(slash[slashIdx]); return; }
            if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); setSlashDismissed(true); return; }
          }
          if (e.key === 'Backspace' && !value && pastes.length) { e.preventDefault(); setPastes((ps) => ps.slice(0, -1)); return; }
          if (e.key === 'ArrowUp' && !value && !busy && !e.shiftKey && !e.altKey && !e.metaKey && !e.ctrlKey && onEditLast?.()) { e.preventDefault(); return; }
          if (e.key !== 'Enter' || e.nativeEvent.isComposing) return;
          const mod = e.metaKey || e.ctrlKey;
          if (enterToSend ? !e.shiftKey : mod) { e.preventDefault(); submit(); }
        }}
        placeholder={placeholder}
        aria-label="Message"
        aria-controls={slash?.length ? lid : undefined}
        aria-activedescendant={slash?.length ? `${lid}-${slashIdx}` : undefined}
        aria-autocomplete="list"
        enterKeyHint="send"
        className="scrollbar-thin block max-h-[240px] w-full resize-none bg-transparent px-3 pt-1.5 pb-2 text-base leading-6 outline-none focus-visible:outline-none placeholder:text-fg-subtle"
      />
      <div className="flex items-center justify-between">
        <div className="flex min-w-0 items-center gap-1">
          <button type="button" aria-label="Add files" title="Attachments (coming later)" className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-fg-muted hover:bg-hover">
            <Plus size={20} />
          </button>
          <span className="inline-flex min-w-0 items-center gap-1.5 truncate rounded-full border border-line px-3 py-1.5 text-xs text-fg-muted" title="Queries run in a scoped, PHI-safe session">
            <Database size={13} className="shrink-0" /> <span className="truncate">Claims lake · sem.*</span>
          </span>
        </div>
        <div className="relative h-9 w-9 shrink-0">
          {busy ? (
            <button key="stop" type="button" onClick={onStop} aria-label="Stop generating" className="absolute inset-0 flex items-center justify-center rounded-full bg-accent text-accent-fg motion-safe:animate-[cc-pop_150ms_ease-out]">
              <Square size={12} fill="currentColor" />
            </button>
          ) : (
            <button key="send" type="submit" aria-label="Send message" disabled={!canSend}
              className={clsx('absolute inset-0 flex items-center justify-center rounded-full bg-accent text-accent-fg transition-[opacity,transform] duration-150 motion-safe:animate-[cc-pop_150ms_ease-out]', canSend ? 'opacity-100 active:scale-95' : 'opacity-30')}>
              <ArrowUp size={18} strokeWidth={2.5} />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
