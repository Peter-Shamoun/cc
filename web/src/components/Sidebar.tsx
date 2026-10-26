import { useEffect, useRef, useState } from 'react';
import { Ellipsis, Keyboard, MessageSquarePlus, PanelLeft, Pencil, Pin, PinOff, Search, Settings, ShieldCheck, Share, SquarePen, Trash2, X } from 'lucide-react';
import clsx from 'clsx';
import { IconButton } from './ui';
import { Menu, modKey, useMenu, type MenuItem } from '../shell/primitives';
import type { ThreadSummary } from '../adapters/types';

const DAY = 86400000;
/** ChatGPT-style date buckets, computed against local midnight (not a rolling 24h). */
export function bucket(iso: string, now = new Date()): string {
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  const t = Date.parse(iso);
  if (t >= midnight) return 'Today';
  if (t >= midnight - DAY) return 'Yesterday';
  if (t >= midnight - 7 * DAY) return 'Previous 7 days';
  if (t >= midnight - 30 * DAY) return 'Previous 30 days';
  const d = new Date(t);
  return d.getFullYear() === now.getFullYear() ? d.toLocaleString('en-US', { month: 'long' }) : String(d.getFullYear());
}

export function groupThreads(threads: ThreadSummary[], now = new Date()): [string, ThreadSummary[]][] {
  const groups = new Map<string, ThreadSummary[]>();
  const pinned = threads.filter((t) => t.pinned);
  if (pinned.length) groups.set('Pinned', pinned);
  for (const t of threads.filter((x) => !x.pinned)) {
    const b = bucket(t.updatedAt, now);
    groups.set(b, [...(groups.get(b) ?? []), t]);
  }
  return [...groups.entries()];
}

export interface SidebarProps {
  threads: ThreadSummary[];
  activeId?: string | null;
  storeMode: string;
  drawer?: boolean;
  canPin?: boolean;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  onRename: (id: string, title: string) => void;
  onPin?: (id: string, pinned: boolean) => void;
  onExport?: (id: string) => void;
  onCollapse: () => void;
  onSearch: () => void;
  onSettings: () => void;
  onShortcuts: () => void;
}

/** Full sidebar (ChatGPT/Claude): new chat, search, date-grouped threads with hover "…" menu, account row. */
export function Sidebar(p: SidebarProps) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const account = useMenu();
  const groups = groupThreads(p.threads);
  // Roving focus: one tab stop for the whole list; arrows move between chats.
  const onListKey = (e: React.KeyboardEvent) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key) || (e.target as HTMLElement).tagName === 'INPUT') return;
    const items = [...(list.current?.querySelectorAll<HTMLElement>('[data-thread-link]') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    const n = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : e.key === 'ArrowDown' ? Math.min(items.length - 1, i + 1) : Math.max(0, i - 1);
    e.preventDefault();
    items[n]?.focus();
  };
  const activeOrFirst = p.threads.some((t) => t.id === p.activeId) ? p.activeId : p.threads[0]?.id;
  const accountItems: MenuItem[] = [
    { label: 'Settings', icon: <Settings size={16} />, onSelect: p.onSettings, shortcut: `${modKey()} ,` },
    { label: 'Keyboard shortcuts', icon: <Keyboard size={16} />, onSelect: p.onShortcuts, shortcut: `${modKey()} /` },
  ];
  const Root = p.drawer ? 'div' : 'aside';
  return (
    <Root className={clsx('flex h-full w-[260px] shrink-0 flex-col bg-sidebar font-sans', p.drawer && 'fixed inset-y-0 left-0 z-40 w-[min(300px,85vw)] shadow-2xl motion-safe:animate-[cc-drawer_200ms_var(--ease-out)]')}
      data-testid="sidebar" aria-modal={p.drawer || undefined} role={p.drawer ? 'dialog' : undefined} aria-label="Chat history" tabIndex={p.drawer ? -1 : undefined}>
      <div className="flex h-[52px] shrink-0 items-center justify-between px-2">
        <div className="flex items-center gap-2 px-2 font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-[11px] font-bold text-accent-fg" aria-hidden>CC</span>
          ClaimClarity
        </div>
        <IconButton label="Close sidebar" onClick={p.onCollapse}>{p.drawer ? <X size={20} /> : <PanelLeft size={18} />}</IconButton>
      </div>
      <nav className="px-2 text-sm" aria-label="Chat actions">
        <button type="button" onClick={p.onNew} className="group/nav flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 hover:bg-hover">
          <SquarePen size={17} /> <span className="flex-1 text-left">New chat</span>
          <kbd className="hidden font-sans text-xs text-fg-subtle group-hover/nav:inline">{modKey()} ⇧ O</kbd>
        </button>
        <button type="button" onClick={p.onSearch} className="group/nav flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 hover:bg-hover">
          <Search size={17} /> <span className="flex-1 text-left">Search chats</span>
          <kbd className="hidden font-sans text-xs text-fg-subtle group-hover/nav:inline">{modKey()} K</kbd>
        </button>
      </nav>
      <div ref={list} onKeyDown={onListKey} className="scrollbar-thin mt-4 flex-1 overflow-y-auto px-2 text-sm" aria-label="Chats" role="navigation">
        {p.threads.length === 0 && <div className="px-2.5 text-xs text-fg-subtle">No chats yet</div>}
        {groups.map(([g, items]) => (
          <section key={g} className="mb-5" aria-label={g}>
            <h3 className="sticky top-0 z-[1] bg-sidebar px-2.5 pb-1 pt-1 text-xs font-medium text-fg-subtle">{g}</h3>
            <ul>
              {items.map((t) => (
                <ThreadRow key={t.id} t={t} active={t.id === p.activeId} tabStop={t.id === activeOrFirst} renaming={renaming === t.id}
                  setRenaming={setRenaming} {...p} />
              ))}
            </ul>
          </section>
        ))}
      </div>
      <div className="shrink-0 border-t border-line/60 p-2">
        <div className="mb-1 flex items-start gap-2 px-2.5 py-1.5 text-xs text-fg-subtle" data-testid="history-note">
          <ShieldCheck size={14} className="mt-px shrink-0" />
          <span>{p.storeMode === 'ephemeral' ? <><b className="font-medium text-fg-muted">History off</b> · chats live in memory only.</> : 'History on'} Synthetic demo data.</span>
        </div>
        <button type="button" onClick={account.toggle} aria-haspopup="menu" aria-expanded={!!account.anchor} className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left text-sm hover:bg-hover" data-testid="account-button">
          <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-[#4352d6] text-[11px] font-semibold text-white" aria-hidden>DU</span>
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium">Demo user</span>
            <span className="block truncate text-xs text-fg-subtle">acme_labs · analyst</span>
          </span>
        </button>
        {account.anchor && <Menu label="Account" anchor={account.anchor} items={accountItems} onClose={account.close} side="top" />}
      </div>
    </Root>
  );
}

function ThreadRow({ t, active, tabStop, renaming, setRenaming, onSelect, onRename, onDelete, onPin, onExport, canPin }: SidebarProps & { t: ThreadSummary; active: boolean; tabStop: boolean; renaming: boolean; setRenaming: (id: string | null) => void }) {
  const menu = useMenu();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (renaming) input.current?.select(); }, [renaming]);
  const items: MenuItem[] = [
    ...(onExport ? [{ label: 'Export as Markdown', icon: <Share size={16} />, onSelect: () => onExport(t.id) }] : []),
    { label: 'Rename', icon: <Pencil size={16} />, onSelect: () => setRenaming(t.id) },
    ...(canPin && onPin ? [{ label: t.pinned ? 'Unpin' : 'Pin', icon: t.pinned ? <PinOff size={16} /> : <Pin size={16} />, onSelect: () => onPin(t.id, !t.pinned) }] : []),
    'sep',
    { label: 'Delete', icon: <Trash2 size={16} />, danger: true, onSelect: () => onDelete(t.id) },
  ];
  return (
    <li className={clsx('group relative flex items-center rounded-lg', active ? 'bg-hover' : 'hover:bg-hover', menu.anchor && 'bg-hover')} data-testid="thread-item">
      {renaming ? (
        <input ref={input} defaultValue={t.title} aria-label="Rename chat" className="m-0.5 min-w-0 flex-1 rounded-md border border-[var(--focus)] bg-bg px-2 py-1.5 outline-none"
          onBlur={(e) => { onRename(t.id, e.currentTarget.value); setRenaming(null); }}
          onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') { e.currentTarget.value = t.title; setRenaming(null); } }} />
      ) : (
        <button type="button" data-thread-link={t.id} tabIndex={tabStop ? 0 : -1} aria-current={active ? 'page' : undefined} onClick={() => onSelect(t.id)} onDoubleClick={() => setRenaming(t.id)}
          onKeyDown={(e) => { if (e.key === 'F2') { e.preventDefault(); setRenaming(t.id); } if (e.key === 'Delete') { e.preventDefault(); onDelete(t.id); } }}
          className="min-w-0 flex-1 overflow-hidden whitespace-nowrap py-2 pl-2.5 pr-8 text-left [mask-image:linear-gradient(to_right,black_85%,transparent)] group-hover:[mask-image:linear-gradient(to_right,black_70%,transparent)]" title={t.title}>
          {t.pinned && <Pin size={12} className="mr-1.5 inline -translate-y-px text-fg-subtle" aria-label="Pinned" />}
          {t.title}
        </button>
      )}
      {!renaming && (
        <IconButton label={`Options for ${t.title}`} aria-haspopup="menu" aria-expanded={!!menu.anchor} onClick={menu.toggle}
          className={clsx('absolute right-1 h-7 w-7 opacity-0 focus-visible:opacity-100 group-hover:opacity-100 max-md:opacity-100', menu.anchor && 'opacity-100')}>
          <Ellipsis size={16} />
        </IconButton>
      )}
      {menu.anchor && <Menu label="Chat options" anchor={menu.anchor} items={items} onClose={menu.close} />}
    </li>
  );
}

/** Collapsed desktop rail (Claude-style): icons only, the sidebar's main actions stay one click away. */
export function SidebarRail({ onExpand, onNew, onSearch, onSettings }: { onExpand: () => void; onNew: () => void; onSearch: () => void; onSettings: () => void }) {
  return (
    <aside className="flex h-full w-[52px] shrink-0 flex-col items-center gap-1 border-r border-line/60 bg-sidebar py-2.5 font-sans max-md:hidden" aria-label="Sidebar (collapsed)" data-testid="sidebar-rail">
      <IconButton label="Open sidebar" onClick={onExpand}><PanelLeft size={18} /></IconButton>
      <IconButton label="New chat" onClick={onNew}><MessageSquarePlus size={18} /></IconButton>
      <IconButton label="Search chats" onClick={onSearch}><Search size={18} /></IconButton>
      <div className="flex-1" />
      <IconButton label="Settings" onClick={onSettings}><Settings size={18} /></IconButton>
    </aside>
  );
}
