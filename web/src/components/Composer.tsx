import { useEffect, useId, useRef, useState } from 'react';
import { ArrowUp, ChevronDown, Plus, Square, Database, X } from 'lucide-react';
import clsx from 'clsx';
import { getDraft, setDraft } from '../shell/drafts';
import { matchSlash, type SlashCommand } from '../shell/slash';
import { ScopePopover } from '../shell/ScopePopover';

/** Pastes this long become an attachment chip instead of flooding the box (Claude-style). */
export const PASTE_CHARS = 1200;
export const PASTE_LINES = 15;
/** Text files the composer can attach as chips (read locally, kept in memory only). */
export const ATTACH_ACCEPT = '.txt,.csv,.tsv,.md,.sql,.json,.log,text/plain,text/csv,text/markdown,application/json';
export const ATTACH_MAX = 256 * 1024;
const TEXT_EXT = /\.(txt|csv|tsv|md|sql|json|log)$/i;

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
  /** Offline: the draft stays editable, sending waits until the browser reconnects. */
  offline?: boolean;
}

/**
 * ChatGPT/Claude-style composer: auto-growing textarea, send button that morphs into stop while a
 * reply streams, Enter vs Shift+Enter (configurable), IME-safe, per-chat drafts and a "/" command menu.
 */
export function Composer({ onSend, onStop, busy, autoFocus, initial, placeholder = 'Ask about your claims', draftKey, enterToSend = true, onCommand, onEditLast, offline }: ComposerProps) {
  const [value, setValueState] = useState(() => (draftKey ? getDraft(draftKey) : ''));
  const ref = useRef<HTMLTextAreaElement>(null);
  const [slashIdx, setSlashIdx] = useState(0);
  const [slashDismissed, setSlashDismissed] = useState(false);
  const lid = useId();
  // Long pastes ride along as chips; they live in component memory only (never drafts or storage).
  const [pastes, setPastes] = useState<{ id: number; text: string; name?: string }[]>([]);
  const [notice, setNotice] = useState('');
  const [dragging, setDragging] = useState(false);
  const [scopeAnchor, setScopeAnchor] = useState<HTMLElement | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const addFiles = async (files: FileList | File[]) => {
    const skipped: string[] = [];
    for (const f of [...files]) {
      if (!(f.type.startsWith('text/') || f.type === 'application/json' || TEXT_EXT.test(f.name))) { skipped.push(`${f.name} isn't a text file`); continue; }
      if (f.size > ATTACH_MAX) { skipped.push(`${f.name} is over 256 KB`); continue; }
      const text = await f.text();
      setPastes((ps) => [...ps, { id: ++pasteSeq.current, text, name: f.name }]);
    }
    setNotice(skipped.join(' · '));
    ref.current?.focus();
  };
  const pasteSeq = useRef(0);
  const setValue = (t: string) => { setValueState(t); if (draftKey) setDraft(draftKey, t); };
  useEffect(() => { setValueState(draftKey ? getDraft(draftKey) : ''); setPastes([]); setNotice(''); }, [draftKey]);
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
    if ((!value.trim() && !pastes.length) || busy || offline) return;
    onSend([value.trim(), ...pastes.map((x) => (x.name ? `${x.name}:\n${x.text}` : x.text))].filter(Boolean).join('\n\n'));
    setNotice('');
    setValue('');
    setPastes([]);
  };
  const canSend = (!!value.trim() || pastes.length > 0) && !offline;
  const removePaste = (id: number) => { setPastes((ps) => ps.filter((x) => x.id !== id)); ref.current?.focus(); };
  return (
    <form
      onSubmit={(e) => { e.preventDefault(); submit(); }}
      className="relative w-full cursor-text rounded-[28px] bg-elev data-[dragging]:shadow-[0_0_0_2px_var(--focus)] data-[dragging]:bg-hover p-2.5 font-sans shadow-[var(--shadow-composer)] transition-shadow duration-150 focus-within:shadow-[0_0_0_2px_var(--focus),0_6px_20px_rgba(0,0,0,0.08)]"
      onClick={(e) => { if (e.target === e.currentTarget) ref.current?.focus(); }}
      onDragOver={(e) => { if (e.dataTransfer.types.includes('Files')) { e.preventDefault(); setDragging(true); } }}
      onDragLeave={(e) => { if (!e.currentTarget.contains(e.relatedTarget as Node)) setDragging(false); }}
      onDrop={(e) => { if (e.dataTransfer.files.length) { e.preventDefault(); setDragging(false); void addFiles(e.dataTransfer.files); } }}
      data-dragging={dragging || undefined}
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
        <ul className="flex gap-2 overflow-x-auto px-1 pb-2 pt-1 scrollbar-none" aria-label="Attachments" data-testid="paste-chips">
          {pastes.map((x) => {
            const lines = x.text.split('\n').length;
            return (
              <li key={x.id} className="group/chip relative flex h-[88px] w-[132px] shrink-0 flex-col justify-between overflow-hidden rounded-xl border border-line bg-bg p-2.5 text-left motion-safe:animate-[cc-pop_150ms_ease-out]" data-testid="paste-chip">
                <p className={clsx('break-all font-mono text-[10.5px] leading-[14px] text-fg-muted', x.name ? 'line-clamp-2' : 'line-clamp-3')} aria-hidden>{x.text.slice(0, 160)}</p>
                {x.name && <span className="truncate text-[11px] font-medium text-fg" aria-hidden title={x.name}>{x.name}</span>}
                <span className="mt-1 inline-flex w-fit rounded border border-line px-1 text-[10px] font-semibold uppercase tracking-wide text-fg-subtle" aria-hidden>{x.name ? (x.name.split('.').pop() ?? 'file') : 'Pasted'}</span>
                <span className="sr-only">{x.name ? `File ${x.name}` : 'Pasted text'}, {lines} lines, {x.text.length} characters</span>
                <button type="button" onClick={() => removePaste(x.id)} aria-label={x.name ? `Remove ${x.name}` : `Remove pasted text (${lines} lines)`}
                  className="absolute right-1 top-1 flex h-5 w-5 items-center justify-center rounded-full border border-line bg-elev text-fg-muted opacity-0 shadow-sm hover:text-fg focus-visible:opacity-100 group-hover/chip:opacity-100 max-md:opacity-100">
                  <X size={12} strokeWidth={2.5} />
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {notice && <p role="status" className="px-3 pb-1 text-xs text-[var(--danger)]" data-testid="attach-notice">{notice}</p>}
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
          <button type="button" aria-label="Attach text files" title="Attach text files (.txt, .csv, .md, .sql, .json)" onClick={() => fileInput.current?.click()} className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full text-fg-muted hover:bg-hover">
            <Plus size={20} />
          </button>
          <input ref={fileInput} type="file" multiple accept={ATTACH_ACCEPT} className="hidden" tabIndex={-1} aria-hidden data-testid="attach-input"
            onChange={(e) => { if (e.currentTarget.files?.length) void addFiles(e.currentTarget.files); e.currentTarget.value = ''; }} />
          <button type="button" aria-haspopup="dialog" aria-expanded={!!scopeAnchor} data-testid="scope-chip"
            onClick={(e) => { const t = e.currentTarget; setScopeAnchor((a) => (a ? null : t)); }}
            className={clsx('inline-flex min-w-0 items-center gap-1.5 rounded-full border border-line px-3 py-1.5 text-xs text-fg-muted transition-colors hover:bg-hover hover:text-fg', scopeAnchor && 'bg-hover text-fg')}>
            <Database size={13} className="shrink-0" aria-hidden /> <span className="truncate">Claims lake · sem.*</span>
            <ChevronDown size={12} aria-hidden className={clsx('shrink-0 transition-transform', scopeAnchor && 'rotate-180')} />
          </button>
          {scopeAnchor && <ScopePopover anchor={scopeAnchor} onClose={() => setScopeAnchor(null)} />}
        </div>
        <div className="relative h-9 w-9 shrink-0">
          {busy ? (
            <button key="stop" type="button" onClick={onStop} aria-label="Stop generating" title="Stop (Esc)" className="absolute inset-0 flex items-center justify-center rounded-full bg-accent text-accent-fg motion-safe:animate-[cc-pop_150ms_ease-out]">
              <Square size={12} fill="currentColor" />
            </button>
          ) : (
            <button key="send" type="submit" aria-label="Send message" disabled={!canSend} title={offline ? "You're offline" : undefined}
              className={clsx('absolute inset-0 flex items-center justify-center rounded-full bg-accent text-accent-fg transition-[opacity,transform] duration-150 motion-safe:animate-[cc-pop_150ms_ease-out]', canSend ? 'opacity-100 active:scale-95' : 'opacity-30')}>
              <ArrowUp size={18} strokeWidth={2.5} />
            </button>
          )}
        </div>
      </div>
    </form>
  );
}
