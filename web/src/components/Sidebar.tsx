import { useEffect, useState } from 'react';
import { MessageSquarePlus, PanelLeft, Pencil, Search, Trash2, ShieldCheck, X } from 'lucide-react';
import clsx from 'clsx';
import { IconButton } from './ui';

type Item = { id: string; title: string; updatedAt: string };

function bucket(iso: string) {
  const d = (Date.now() - Date.parse(iso)) / 86400000;
  return d < 1 ? 'Today' : d < 2 ? 'Yesterday' : d < 7 ? 'Previous 7 days' : 'Older';
}

export function Sidebar({ threads, activeId, onSelect, onNew, onDelete, onCollapse, onRename, storeMode, drawer, searchSignal = 0 }: { storeMode: string; drawer?: boolean; searchSignal?: number; onRename: (id: string, title: string) => void; threads: Item[]; activeId?: string | null; onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void; onCollapse: () => void }) {
  const [query, setQuery] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  useEffect(() => { if (searchSignal) setQuery(''); }, [searchSignal]);
  const q = query?.trim().toLowerCase() ?? '';
  const groups = new Map<string, Item[]>();
  // Title search, client-side over the in-memory thread list (titles never leave the page).
  for (const t of threads.filter((x) => !q || x.title.toLowerCase().includes(q))) {
    const b = bucket(t.updatedAt);
    groups.set(b, [...(groups.get(b) ?? []), t]);
  }
  return (
    <aside className={clsx('flex h-full w-[260px] font-sans shrink-0 flex-col bg-sidebar', drawer && 'fixed inset-y-0 left-0 z-40 w-[min(300px,85vw)] shadow-2xl')} data-testid="sidebar" aria-modal={drawer || undefined} role={drawer ? 'dialog' : undefined} aria-label="Chat history">
      <div className="flex h-14 items-center justify-between px-3">
        <div className="flex items-center gap-2 px-1 font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-[11px] font-bold text-accent-fg">CC</span>
          ClaimClarity
        </div>
        <IconButton label="Close sidebar" onClick={onCollapse}>
          <PanelLeft size={18} />
        </IconButton>
      </div>
      <nav className="px-2 text-sm">
        <button type="button" onClick={onNew} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 hover:bg-hover">
          <MessageSquarePlus size={17} /> New chat
        </button>
        {query === null ? (
          <button type="button" onClick={() => setQuery('')} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 hover:bg-hover">
            <Search size={17} /> Search chats
          </button>
        ) : (
          <div className="flex items-center gap-2 rounded-lg bg-hover px-2.5 py-1.5">
            <Search size={16} className="shrink-0 text-fg-muted" />
            <input autoFocus value={query} onChange={(e) => setQuery(e.target.value)} onKeyDown={(e) => e.key === 'Escape' && setQuery(null)} placeholder="Search chats" aria-label="Search chats" className="min-w-0 flex-1 bg-transparent outline-none" />
            <IconButton label="Clear search" className="h-6 w-6" onClick={() => setQuery(null)}><X size={14} /></IconButton>
          </div>
        )}
      </nav>
      <div className="mt-4 flex-1 overflow-y-auto px-2 text-sm">
        {threads.length === 0 && <div className="px-2.5 text-xs text-fg-subtle">No chats yet</div>}
        {threads.length > 0 && groups.size === 0 && <div className="px-2.5 text-xs text-fg-subtle">No chats match “{query}”</div>}
        {[...groups.entries()].map(([g, items]) => (
          <div key={g} className="mb-4">
            <div className="px-2.5 pb-1 text-xs font-medium text-fg-subtle">{g}</div>
            {items.map((t) => (
              <div key={t.id} className={clsx('group flex items-center rounded-lg pr-1', t.id === activeId ? 'bg-hover' : 'hover:bg-hover')}>
                {renaming === t.id ? (
                  <input autoFocus defaultValue={t.title} aria-label="Rename chat" className="m-1 min-w-0 flex-1 rounded-md border border-accent bg-bg px-1.5 py-1 outline-none"
                    onBlur={(e) => { onRename(t.id, e.currentTarget.value); setRenaming(null); }}
                    onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); if (e.key === 'Escape') setRenaming(null); }} />
                ) : (
                  <button type="button" onClick={() => onSelect(t.id)} onDoubleClick={() => setRenaming(t.id)} className="min-w-0 flex-1 truncate px-2.5 py-2 text-left">
                    {t.title}
                  </button>
                )}
                <IconButton label="Rename chat" onClick={() => setRenaming(t.id)} className="h-7 w-7 opacity-0 group-hover:opacity-100 focus:opacity-100">
                  <Pencil size={13} />
                </IconButton>
                <IconButton label="Delete chat" onClick={() => onDelete(t.id)} className="h-7 w-7 opacity-0 group-hover:opacity-100 focus:opacity-100">
                  <Trash2 size={14} />
                </IconButton>
              </div>
            ))}
          </div>
        ))}
      </div>
      <div className="m-2 flex items-center gap-2 rounded-lg px-2.5 py-2 text-xs text-fg-muted">
        <ShieldCheck size={15} className="shrink-0" />
        <span>{storeMode === 'ephemeral' ? <><b className="font-medium">History off</b> · chats live in memory only.</> : 'History on'} Synthetic demo data.</span>
      </div>
    </aside>
  );
}
