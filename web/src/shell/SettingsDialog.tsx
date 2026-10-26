import { useState, type KeyboardEvent, type ReactNode } from 'react';
import { ArchiveRestore, Database, Keyboard, Monitor, Moon, Settings2, Sun } from 'lucide-react';
import clsx from 'clsx';
import { Dialog, Kbd } from './primitives';
import { useTheme, type ModePref } from '../lib/theme';
import { SHORTCUTS } from './shortcuts';
import type { ThreadSummary } from '../adapters/types';

type Tab = 'general' | 'data' | 'shortcuts';

/** ChatGPT-style settings: tabs on the left, rows of label + control on the right. */
export function SettingsDialog({ onClose, initialTab = 'general', storeMode, enterToSend, setEnterToSend, onExportAll, threadCount, archived, onUnarchive, onOpenChat }: { onClose: () => void; initialTab?: Tab; storeMode: string; enterToSend: boolean; setEnterToSend: (v: boolean) => void; onExportAll: () => void; threadCount: number; archived?: ThreadSummary[]; onUnarchive?: (id: string) => void; onOpenChat?: (id: string) => void }) {
  const [showArchived, setShowArchived] = useState(false);
  const [tab, setTab] = useState<Tab>(initialTab);
  const { preference, setPreference, palette, setPalette } = useTheme();
  const tabs: [Tab, string, ReactNode][] = [['general', 'General', <Settings2 size={16} />], ['data', 'Data controls', <Database size={16} />], ['shortcuts', 'Shortcuts', <Keyboard size={16} />]];
  return (
    <Dialog title="Settings" onClose={onClose} testId="settings-dialog">
      <div className="mt-3 flex flex-col border-t border-line/70 max-sm:flex-1 sm:h-[min(440px,70vh)] sm:flex-row">
        {/* oxlint-disable-next-line jsx-a11y/interactive-supports-focus -- arrow keys delegated from the focused tab (roving tabindex) */}
        <div role="tablist" aria-orientation="vertical" aria-label="Settings sections" className="flex shrink-0 gap-0.5 overflow-x-auto scrollbar-none p-2 max-sm:border-b max-sm:border-line/70 sm:w-44 sm:flex-col sm:border-r sm:border-line/70"
          onKeyDown={(e) => {
            const order = tabs.map((t) => t[0]);
            const i = order.indexOf(tab);
            const n = e.key === 'ArrowDown' || e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowUp' || e.key === 'ArrowLeft' ? i - 1 : null;
            if (n === null) return;
            e.preventDefault();
            const next = order[(n + order.length) % order.length];
            setTab(next);
            document.getElementById(`settings-tab-${next}`)?.focus();
          }}>
          {tabs.map(([id, label, icon]) => (
            <button key={id} id={`settings-tab-${id}`} role="tab" type="button" aria-selected={tab === id} aria-controls={`settings-panel-${id}`} tabIndex={tab === id ? 0 : -1} data-autofocus={tab === id ? '' : undefined} onClick={() => setTab(id)}
              className={clsx('flex shrink-0 items-center gap-2 whitespace-nowrap rounded-lg px-2.5 py-1.5 text-left text-sm', id === 'shortcuts' && 'max-sm:hidden', tab === id ? 'bg-hover font-medium text-fg' : 'text-fg-muted hover:bg-hover hover:text-fg')}>
              {icon}{label}
            </button>
          ))}
        </div>
        <div id={`settings-panel-${tab}`} role="tabpanel" aria-labelledby={`settings-tab-${tab}`} tabIndex={0} className="scrollbar-thin outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-[var(--focus)] min-w-0 flex-1 overflow-y-auto px-5 pb-4 pt-3 text-sm" data-testid="settings-panel">
          <h3 className="pb-1 text-[15px] font-semibold max-sm:sr-only">{tabs.find((t) => t[0] === tab)?.[1]}</h3>
          {tab === 'general' && (
            <>
              <Row label="Theme" hint="System follows your OS setting.">
                <Segmented value={preference} onChange={(v) => setPreference(v as ModePref)} label="Theme"
                  options={[['system', 'System', <Monitor size={14} />], ['light', 'Light', <Sun size={14} />], ['dark', 'Dark', <Moon size={14} />]]} />
              </Row>
              <Row label="Style" hint="Neutral is sans and grey; Paper is warm with a serif reading face.">
                <Segmented value={palette} onChange={(v) => setPalette(v as 'neutral' | 'paper')} label="Style" options={[['neutral', 'Neutral'], ['paper', 'Paper']]} />
              </Row>
              <Row label="Send with Enter" hint={enterToSend ? 'Enter sends, Shift+Enter adds a line.' : 'Ctrl/⌘+Enter sends, Enter adds a line.'}>
                <Switch checked={enterToSend} onChange={setEnterToSend} label="Send with Enter" />
              </Row>
            </>
          )}
          {tab === 'data' && (
            <>
              <Group label="Storage" />
              <Row label="Chat history" hint={storeMode === 'ephemeral' ? 'Off. Chats live in this tab’s memory and are gone on reload. Nothing is written to browser storage.' : 'On. Chats are stored encrypted server-side.'}>
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-fg-muted">{storeMode === 'ephemeral' ? 'Memory only' : 'Server'}</span>
              </Row>
              <Row label="Unsent drafts" hint="Kept per chat in memory only, never in localStorage, because a draft can contain PHI.">
                <span className="rounded-full bg-muted px-2 py-0.5 text-xs text-fg-muted">Memory only</span>
              </Row>
              {archived && (
                <>
                  <Group label="Chats" />
                  <Row label="Archived chats" hint={archived.length ? `${archived.length} archived. They stay searchable and keep their history.` : 'Archive a chat from its … menu in the sidebar.'}>
                    <button type="button" disabled={!archived.length} aria-expanded={showArchived} aria-controls="archived-list" onClick={() => setShowArchived((v) => !v)}
                      className="rounded-full border border-line px-3 py-1 text-[13px] hover:bg-hover disabled:opacity-40" data-testid="archived-manage">{showArchived && archived.length ? 'Hide' : 'Manage'}</button>
                  </Row>
                  {showArchived && archived.length > 0 && (
                    <ul id="archived-list" aria-label="Archived chats" className="mb-2 divide-y divide-line/60 rounded-lg border border-line/70" data-testid="archived-list">
                      {archived.map((t) => (
                        <li key={t.id} className="flex items-center gap-2 py-1 pl-3 pr-1">
                          <button type="button" onClick={() => onOpenChat?.(t.id)} className="min-w-0 flex-1 truncate py-1 text-left hover:underline" title={t.title}>{t.title}</button>
                          <button type="button" onClick={() => onUnarchive?.(t.id)} aria-label={`Unarchive ${t.title}`} title="Unarchive"
                            className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-fg-muted hover:bg-hover hover:text-fg"><ArchiveRestore size={15} aria-hidden /></button>
                        </li>
                      ))}
                    </ul>
                  )}
                </>
              )}
              <Group label="Export" />
              <Row label="Export chats" hint="Download every chat in this session as one Markdown file. PHI-flagged table columns are dropped.">
                <button type="button" disabled={!threadCount} onClick={onExportAll} className="rounded-full border border-line px-3 py-1 text-[13px] hover:bg-hover disabled:opacity-40">Export</button>
              </Row>
            </>
          )}
          {tab === 'shortcuts' && <ShortcutList />}
        </div>
      </div>
    </Dialog>
  );
}

export function ShortcutList({ columns = false }: { columns?: boolean }) {
  const groups = (['Chats', 'Navigation', 'Composer'] as const).map((g) => [g, SHORTCUTS.filter((s) => s.group === g)] as const);
  return (
    <div className={clsx('grid gap-x-8 gap-y-4', columns && 'md:grid-cols-2')} data-testid="shortcut-list">
      {groups.map(([g, items]) => (
        <section key={g} aria-label={g} className={clsx(columns && g === 'Navigation' && 'md:row-span-2')}>
          <h3 className="pb-1 text-xs font-medium text-fg-subtle">{g}</h3>
          <ul className="divide-y divide-line/70">
            {items.map((s) => (
              <li key={s.label} className="flex items-center justify-between gap-4 py-2">
                <span>{s.label}</span>
                <span className="flex shrink-0 gap-1">{s.keys().map((k) => <Kbd key={k}>{k}</Kbd>)}</span>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog title="Keyboard shortcuts" onClose={onClose} className="max-w-[720px]" testId="shortcuts-dialog">
      <div className="px-5 pb-5 pt-3 text-sm"><ShortcutList columns /></div>
    </Dialog>
  );
}

/** Quiet sub-heading inside a settings panel (ChatGPT-style grouping without boxes). */
function Group({ label }: { label: string }) {
  return <h4 className="pb-0.5 pt-4 text-xs font-medium text-fg-subtle first:pt-1">{label}</h4>;
}

/** One setting: label (+ one-line hint) left, control right, hairline between rows. Dense: ~52px per row. */
function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex min-h-[52px] items-center justify-between gap-6 border-b border-line/60 py-2.5 last:border-0 [&:has(+h4)]:border-0" data-testid="settings-row">
      <div className="min-w-0">
        <div className="leading-5">{label}</div>
        {hint && <div className="mt-0.5 max-w-[42ch] text-xs leading-4 text-fg-subtle">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

// One radio group, one Tab stop (QA-S17): only the checked radio is tabbable; arrows (and Home/End) move
// the selection and focus together, wrapping, mirrored in RTL — the native radio-group pattern.
function Segmented({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: [string, string, ReactNode?][]; label: string }) {
  const values = options.map(([v]) => v);
  const tabStop = values.includes(value) ? value : values[0];
  const onKeyDown = (e: KeyboardEvent<HTMLButtonElement>) => {
    const i = Math.max(0, values.indexOf(value));
    const rtl = getComputedStyle(e.currentTarget).direction === 'rtl';
    const step = { ArrowDown: 1, ArrowUp: -1, ArrowRight: rtl ? -1 : 1, ArrowLeft: rtl ? 1 : -1 }[e.key];
    const next = e.key === 'Home' ? 0 : e.key === 'End' ? values.length - 1 : step === undefined ? -1 : (i + step + values.length) % values.length;
    if (next < 0) return;
    e.preventDefault();
    onChange(values[next]);
    e.currentTarget.parentElement?.querySelectorAll<HTMLElement>('[role="radio"]')[next]?.focus();
  };
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-lg bg-muted p-0.5 text-xs">
      {options.map(([v, l, icon]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} tabIndex={v === tabStop ? 0 : -1} onKeyDown={onKeyDown} onClick={() => onChange(v)}
          className={clsx('flex items-center gap-1.5 rounded-md px-2.5 py-1.5 transition-colors', value === v ? 'bg-elev font-medium shadow-sm' : 'text-fg-muted hover:text-fg')}>
          {icon}{l}
        </button>
      ))}
    </div>
  );
}

function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <button type="button" role="switch" aria-checked={checked} aria-label={label} onClick={() => onChange(!checked)}
      className={clsx('relative h-6 w-10 rounded-full transition-colors', checked ? 'bg-[var(--focus)]' : 'bg-line-strong')}>
      <span className={clsx('absolute left-0 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform duration-150', checked ? 'translate-x-[18px]' : 'translate-x-0.5')} />
    </button>
  );
}
