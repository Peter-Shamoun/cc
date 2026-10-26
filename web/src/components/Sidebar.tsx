import { useEffect, useRef, useState } from 'react';
import { Archive, Ellipsis, Filter, Keyboard, LogOut, MessageSquarePlus, PanelLeft, Pencil, Pin, PinOff, Search, Settings, ShieldCheck, Share, SquarePen, Trash2, X } from 'lucide-react';
import clsx from 'clsx';
import { IconButton } from './ui';
import { Menu, modKey, useMenu, type MenuItem } from '../shell/primitives';
import type { ThreadSummary } from '../adapters/types';

/**
 * ChatGPT-style date buckets, by local calendar day (not a rolling 24 h). Day boundaries are built with `new Date(y,
 * m, d - n)`, never `midnight - n * 24 h`: across a DST change a day is 23 or 25 hours long, and the fixed-length
 * version put a chat from 23:30 two days ago under "Yesterday". An unparseable timestamp goes to "Older", not "NaN".
 */
export function bucket(iso: string, now = new Date()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 'Older';
  const dayStart = (back: number) => new Date(now.getFullYear(), now.getMonth(), now.getDate() - back).getTime();
  if (t >= dayStart(0)) return 'Today';
  if (t >= dayStart(1)) return 'Yesterday';
  if (t >= dayStart(7)) return 'Previous 7 days';
  if (t >= dayStart(30)) return 'Previous 30 days';
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

/** Case- and accent-insensitive title match for the inline filter. */
const fold = (x: string) => x.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
export function filterThreads(threads: ThreadSummary[], q: string): ThreadSummary[] {
  const words = fold(q).split(/\s+/).filter(Boolean);
  if (!words.length) return threads;
  return threads.filter((t) => { const title = fold(t.title || ''); return words.every((w) => title.includes(w)); });
}
/**
 * QA-S39: ChatGPT/Claude have one search entry. "Search chats" is ours; the inline title filter only joins it once the
 * history is long enough that scanning the list stops working.
 */
export const FILTER_AT = 20;

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
  /** Archive a chat (it leaves the list; Settings → Data controls → Archived chats brings it back). */
  onArchive?: (id: string) => void;
  onExport?: (id: string) => void;
  onCollapse: () => void;
  onSearch: () => void;
  onSettings: () => void;
  onShortcuts: () => void;
  /** Present when there is a session to end (cookie or demo token); absent for the offline mock. */
  onSignOut?: () => void;
}

/** Synthetic demo identity (no real user data in the client). */
export const ACCOUNT = { initials: 'DU', name: 'Demo user', detail: 'acme_labs · analyst' } as const;

function Avatar() {
  return <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-fg/80 text-[11px] font-semibold text-bg" aria-hidden>{ACCOUNT.initials}</span>;
}

type AccountActions = Pick<SidebarProps, 'onSettings' | 'onShortcuts' | 'onSignOut'>;
/** One account menu for the full sidebar and the collapsed rail. */
export function accountMenuItems(p: AccountActions): MenuItem[] {
  return [
    { label: 'Settings', icon: <Settings size={16} />, onSelect: p.onSettings, shortcut: `${modKey()} ,` },
    { label: 'Keyboard shortcuts', icon: <Keyboard size={16} />, onSelect: p.onShortcuts, shortcut: `${modKey()} /` },
    ...(p.onSignOut ? [{ label: 'Sign out', icon: <LogOut size={16} />, onSelect: p.onSignOut }] : []),
  ];
}

/** Full sidebar (ChatGPT/Claude): new chat, search, date-grouped threads with hover "…" menu, account row. */
export function Sidebar(p: SidebarProps) {
  const [renaming, setRenaming] = useState<string | null>(null);
  const list = useRef<HTMLDivElement>(null);
  const account = useMenu();
  const [q, setQ] = useState('');
  const showFilter = p.threads.length >= FILTER_AT;
  const shown = showFilter ? filterThreads(p.threads, q) : p.threads;
  const groups = groupThreads(shown);
  const filterKey = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Escape' && q) { e.preventDefault(); e.stopPropagation(); setQ(''); }
    else if (e.key === 'ArrowDown') { e.preventDefault(); list.current?.querySelector<HTMLElement>('[data-thread-link]')?.focus(); }
    else if (e.key === 'Enter') { const first = shown[0]; if (first && q) { e.preventDefault(); e.currentTarget.blur(); p.onSelect(first.id); } }
  };
  // Roving focus: one tab stop for the whole list; arrows move between chats.
  const onListKey = (e: React.KeyboardEvent) => {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(e.key) || (e.target as HTMLElement).tagName === 'INPUT') return;
    const items = [...(list.current?.querySelectorAll<HTMLElement>('[data-thread-link]') ?? [])];
    const i = items.indexOf(document.activeElement as HTMLElement);
    const n = e.key === 'Home' ? 0 : e.key === 'End' ? items.length - 1 : e.key === 'ArrowDown' ? Math.min(items.length - 1, i + 1) : Math.max(0, i - 1);
    e.preventDefault();
    items[n]?.focus();
  };
  const activeOrFirst = shown.some((t) => t.id === p.activeId) ? p.activeId : shown[0]?.id;
  const accountItems = accountMenuItems(p);
  const Root = p.drawer ? 'div' : 'aside';
  return (
    <Root className={clsx('flex h-full w-[260px] shrink-0 flex-col bg-sidebar font-sans', p.drawer && 'fixed inset-y-0 left-0 z-40 w-[min(300px,85vw)] cc-pan-y shadow-lg motion-safe:animate-[cc-drawer_200ms_var(--ease-out)]')}
      data-testid="sidebar" aria-modal={p.drawer || undefined} role={p.drawer ? 'dialog' : undefined} aria-label="Chat history" tabIndex={p.drawer ? -1 : undefined}>
      <div className="flex h-[52px] shrink-0 items-center justify-between px-2">
        <div className="flex items-center gap-2 px-2 font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-[11px] font-bold text-accent-fg" aria-hidden>CC</span>
          ClaimClarity
        </div>
        <IconButton label="Close sidebar" tip={p.drawer ? undefined : `Close sidebar · ${modKey()} ⇧ S`} onClick={p.onCollapse}>{p.drawer ? <X size={20} /> : <PanelLeft size={18} />}</IconButton>
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
      {showFilter && (
        <div className="relative mx-2 mt-3" role="search">
          <Filter size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-fg-subtle" aria-hidden />
          <input type="search" value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={filterKey} aria-label="Filter chats by title"
            aria-controls="sidebar-chat-list" placeholder="Filter chats" autoComplete="off" spellCheck={false} data-testid="sidebar-filter"
            className="h-8 w-full rounded-lg border border-transparent bg-transparent pl-8 pr-7 text-sm placeholder:text-fg-subtle hover:bg-hover focus:border-line focus:bg-bg focus:outline-none focus-visible:ring-2 focus-visible:ring-[var(--focus)] [&::-webkit-search-cancel-button]:hidden" />
          {q && (
            <button type="button" onClick={() => setQ('')} aria-label="Clear filter" className="absolute right-1.5 top-1/2 flex h-5 w-5 -translate-y-1/2 items-center justify-center rounded text-fg-subtle hover:bg-hover hover:text-fg">
              <X size={13} />
            </button>
          )}
          <span className="sr-only" aria-live="polite">{q ? `${shown.length} ${shown.length === 1 ? 'chat' : 'chats'} match` : ''}</span>
        </div>
      )}
      {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- arrow-key navigation delegated from focused chat links */}
      <div ref={list} id="sidebar-chat-list" onKeyDown={onListKey} className={clsx('scrollbar-thin flex-1 overflow-y-auto px-2 text-sm', showFilter ? 'mt-2' : 'mt-4')} aria-label="Chats" role="navigation">
        {/* No "No chats yet" line: ChatGPT/Claude leave an empty history quiet. */}
        {p.threads.length > 0 && shown.length === 0 && (
          <div className="px-2.5 py-1 text-xs text-fg-subtle" data-testid="filter-empty">
            No chat titles match “{q}”.{' '}
            <button type="button" onClick={p.onSearch} className="font-medium text-fg-muted underline underline-offset-2 hover:text-fg">Search messages</button>
          </div>
        )}
        {groups.map(([g, items]) => (
          <section key={g} className="mb-5" aria-label={g}>
            <h3 className="sticky top-0 z-[1] bg-sidebar px-2.5 pb-1 pt-1 text-xs font-medium text-fg-subtle after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-2 after:bg-linear-to-b after:from-sidebar after:to-transparent">{g}</h3>
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
          <Avatar />
          <span className="min-w-0 flex-1">
            <span className="block truncate font-medium">{ACCOUNT.name}</span>
            <span className="block truncate text-xs text-fg-subtle">{ACCOUNT.detail}</span>
          </span>
        </button>
        {account.anchor && <Menu label="Account" anchor={account.anchor} items={accountItems} onClose={account.close} side="top" />}
      </div>
    </Root>
  );
}

function ThreadRow({ t, active, tabStop, renaming, setRenaming, onSelect, onRename, onDelete, onPin, onArchive, onExport, canPin }: SidebarProps & { t: ThreadSummary; active: boolean; tabStop: boolean; renaming: boolean; setRenaming: (id: string | null) => void }) {
  const menu = useMenu();
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => { if (renaming) input.current?.select(); }, [renaming]);
  const items: MenuItem[] = [
    ...(onExport ? [{ label: 'Export as Markdown', icon: <Share size={16} />, onSelect: () => onExport(t.id) }] : []),
    { label: 'Rename', icon: <Pencil size={16} />, onSelect: () => setRenaming(t.id) },
    ...(canPin && onPin ? [{ label: t.pinned ? 'Unpin' : 'Pin', icon: t.pinned ? <PinOff size={16} /> : <Pin size={16} />, onSelect: () => onPin(t.id, !t.pinned) }] : []),
    ...(onArchive ? [{ label: 'Archive', icon: <Archive size={16} />, onSelect: () => onArchive(t.id) }] : []),
    'sep',
    { label: 'Delete', icon: <Trash2 size={16} />, danger: true, onSelect: () => onDelete(t.id) },
  ];
  return (
    // QA-S38: touch has no hover, so rows stay plain titles (only the open chat shows "…"); a long-press opens
    // any row's options, as ChatGPT/Claude mobile do. Right-click does the same on desktop.
    <li className={clsx('group relative flex items-center rounded-lg', active ? 'bg-hover' : 'hover:bg-hover', menu.anchor && 'bg-hover')} data-testid="thread-item">
      {renaming ? (
        <input ref={input} defaultValue={t.title} aria-label="Rename chat" className="m-0.5 min-w-0 flex-1 rounded-md border border-[var(--focus)] bg-bg px-2 py-1.5 outline-none"
          onBlur={(e) => { onRename(t.id, e.currentTarget.value); setRenaming(null); }}
          onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') { e.currentTarget.value = t.title; setRenaming(null); } }} />
      ) : (
        <button type="button" data-thread-link={t.id} tabIndex={tabStop ? 0 : -1} aria-current={active ? 'page' : undefined} onClick={() => onSelect(t.id)} onDoubleClick={() => setRenaming(t.id)}
          onContextMenu={(e) => { const btn = e.currentTarget.parentElement?.querySelector<HTMLElement>('[data-testid="thread-options"]'); if (!btn) return; e.preventDefault(); menu.openAt(btn); }}
          onKeyDown={(e) => { if (e.key === 'F2') { e.preventDefault(); setRenaming(t.id); } if (e.key === 'Delete') { e.preventDefault(); onDelete(t.id); } }}
          className="min-w-0 flex-1 overflow-hidden whitespace-nowrap py-2 pl-2.5 pr-8 text-left [mask-image:linear-gradient(to_right,black_85%,transparent)] group-hover:[mask-image:linear-gradient(to_right,black_70%,transparent)]" title={t.title}>
          {t.pinned && <Pin size={12} className="mr-1.5 inline -translate-y-px text-fg-subtle" aria-label="Pinned" />}
          {t.title}
        </button>
      )}
      {!renaming && (
        <IconButton label={`Options for ${t.title}`} aria-haspopup="menu" aria-expanded={!!menu.anchor} onClick={menu.toggle}
          className={clsx('absolute right-1 h-7 w-7 opacity-0 focus-visible:opacity-100 group-hover:opacity-100', active ? 'max-md:opacity-100' : '[@media(hover:none)]:pointer-events-none', menu.anchor && 'opacity-100')} data-testid="thread-options">
          <Ellipsis size={16} />
        </IconButton>
      )}
      {menu.anchor && <Menu label="Chat options" anchor={menu.anchor} items={items} onClose={menu.close} />}
    </li>
  );
}

/** Collapsed desktop rail (ChatGPT/Claude): icons only; the account avatar at the bottom opens the same account menu (QA-S40). */
export function SidebarRail({ onExpand, onNew, onSearch, ...acct }: { onExpand: () => void; onNew: () => void; onSearch: () => void } & AccountActions) {
  const account = useMenu();
  return (
    <aside className="flex h-full w-[52px] shrink-0 flex-col items-center gap-1 border-r border-line/60 bg-sidebar py-2.5 font-sans max-md:hidden" aria-label="Sidebar (collapsed)" data-testid="sidebar-rail">
      <IconButton label="Open sidebar" tip={`Open sidebar · ${modKey()} ⇧ S`} tipAt="right" onClick={onExpand}><PanelLeft size={18} /></IconButton>
      <IconButton label="New chat" tip={`New chat · ${modKey()} ⇧ O`} tipAt="right" onClick={onNew}><MessageSquarePlus size={18} /></IconButton>
      <IconButton label="Search chats" tip={`Search chats · ${modKey()} K`} tipAt="right" onClick={onSearch}><Search size={18} /></IconButton>
      <div className="flex-1" />
      <button type="button" onClick={account.toggle} aria-haspopup="menu" aria-expanded={!!account.anchor} aria-label={`Account: ${ACCOUNT.name}`} title={ACCOUNT.name}
        className="flex h-9 w-9 items-center justify-center rounded-lg hover:bg-hover" data-testid="rail-account">
        <Avatar />
      </button>
      {account.anchor && <Menu label="Account" anchor={account.anchor} items={accountMenuItems(acct)} onClose={account.close} side="top" />}
    </aside>
  );
}
