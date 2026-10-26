import { useState, type ReactNode } from 'react';
import { Database, Keyboard, Monitor, Moon, Settings2, Sun } from 'lucide-react';
import clsx from 'clsx';
import { Dialog, Kbd } from './primitives';
import { useTheme, type ModePref } from '../lib/theme';
import { SHORTCUTS } from './shortcuts';

type Tab = 'general' | 'data' | 'shortcuts';

/** ChatGPT-style settings: tabs on the left, rows of label + control on the right. */
export function SettingsDialog({ onClose, initialTab = 'general', storeMode, enterToSend, setEnterToSend, onExportAll, threadCount }: { onClose: () => void; initialTab?: Tab; storeMode: string; enterToSend: boolean; setEnterToSend: (v: boolean) => void; onExportAll: () => void; threadCount: number }) {
  const [tab, setTab] = useState<Tab>(initialTab);
  const { preference, setPreference, palette, setPalette } = useTheme();
  const tabs: [Tab, string, ReactNode][] = [['general', 'General', <Settings2 size={16} />], ['data', 'Data controls', <Database size={16} />], ['shortcuts', 'Shortcuts', <Keyboard size={16} />]];
  return (
    <Dialog title="Settings" onClose={onClose} testId="settings-dialog">
      <div className="flex min-h-[380px] flex-col gap-2 px-3 pb-4 pt-2 sm:flex-row">
        <div role="tablist" aria-orientation="vertical" aria-label="Settings sections" className="flex shrink-0 gap-1 sm:w-44 sm:flex-col"
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
              className={clsx('flex items-center gap-2 rounded-lg px-3 py-2 text-left text-sm', tab === id ? 'bg-hover font-medium' : 'text-fg-muted hover:bg-hover hover:text-fg')}>
              {icon}{label}
            </button>
          ))}
        </div>
        <div id={`settings-panel-${tab}`} role="tabpanel" aria-labelledby={`settings-tab-${tab}`} className="min-w-0 flex-1 px-2 text-sm">
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
              <Row label="Chat history" hint={storeMode === 'ephemeral' ? 'Off. Chats live in this tab’s memory and are gone on reload. Nothing is written to browser storage.' : 'On. Chats are stored encrypted server-side.'}>
                <span className="rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted">{storeMode === 'ephemeral' ? 'Memory only' : 'Server'}</span>
              </Row>
              <Row label="Unsent drafts" hint="Kept per chat in memory only, never in localStorage, because a draft can contain PHI.">
                <span className="rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted">Memory only</span>
              </Row>
              <Row label="Export chats" hint="Download every chat in this session as one Markdown file. PHI-flagged table columns are dropped.">
                <button type="button" disabled={!threadCount} onClick={onExportAll} className="rounded-full border border-line px-3.5 py-1.5 hover:bg-hover disabled:opacity-40">Export</button>
              </Row>
            </>
          )}
          {tab === 'shortcuts' && <ShortcutList />}
        </div>
      </div>
    </Dialog>
  );
}

export function ShortcutList() {
  return (
    <ul className="divide-y divide-line" data-testid="shortcut-list">
      {SHORTCUTS.map((s) => (
        <li key={s.label} className="flex items-center justify-between gap-4 py-2.5">
          <span>{s.label}</span>
          <span className="flex shrink-0 gap-1">{s.keys().map((k) => <Kbd key={k}>{k}</Kbd>)}</span>
        </li>
      ))}
    </ul>
  );
}

export function ShortcutsDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog title="Keyboard shortcuts" onClose={onClose} className="max-w-[480px]" testId="shortcuts-dialog">
      <div className="px-5 pb-4 pt-1 text-sm"><ShortcutList /></div>
    </Dialog>
  );
}

function Row({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 border-b border-line py-3.5 last:border-0">
      <div className="min-w-0">
        <div>{label}</div>
        {hint && <div className="mt-0.5 text-xs text-fg-subtle">{hint}</div>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}

function Segmented({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: [string, string, ReactNode?][]; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} className="flex rounded-lg bg-muted p-0.5 text-xs">
      {options.map(([v, l, icon]) => (
        <button key={v} type="button" role="radio" aria-checked={value === v} onClick={() => onChange(v)}
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
      className={clsx('relative h-6 w-10 rounded-full transition-colors', checked ? 'bg-[#10a37f]' : 'bg-line-strong')}>
      <span className={clsx('absolute left-0 top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform duration-150', checked ? 'translate-x-[18px]' : 'translate-x-0.5')} />
    </button>
  );
}
