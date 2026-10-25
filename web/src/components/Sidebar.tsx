import { MessageSquarePlus, PanelLeft, Search, Trash2, ShieldCheck } from 'lucide-react';
import clsx from 'clsx';
import { IconButton } from './ui';

type Item = { id: string; title: string; updatedAt: string };

function bucket(iso: string) {
  const d = (Date.now() - Date.parse(iso)) / 86400000;
  return d < 1 ? 'Today' : d < 2 ? 'Yesterday' : d < 7 ? 'Previous 7 days' : 'Older';
}

export function Sidebar({ threads, activeId, onSelect, onNew, onDelete, onCollapse, storeMode }: { storeMode: string; threads: Item[]; activeId?: string | null; onSelect: (id: string) => void; onNew: () => void; onDelete: (id: string) => void; onCollapse: () => void }) {
  const groups = new Map<string, Item[]>();
  for (const t of threads) {
    const b = bucket(t.updatedAt);
    groups.set(b, [...(groups.get(b) ?? []), t]);
  }
  return (
    <aside className="flex h-full w-[260px] font-sans shrink-0 flex-col bg-sidebar" data-testid="sidebar">
      <div className="flex h-14 items-center justify-between px-3">
        <div className="flex items-center gap-2 px-1 font-semibold">
          <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-[11px] font-bold text-accent-fg">CC</span>
          CC
        </div>
        <IconButton label="Close sidebar" onClick={onCollapse}>
          <PanelLeft size={18} />
        </IconButton>
      </div>
      <nav className="px-2 text-sm">
        <button type="button" onClick={onNew} className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 hover:bg-hover">
          <MessageSquarePlus size={17} /> New chat
        </button>
        <button type="button" className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-fg-muted hover:bg-hover" title="Not in v0">
          <Search size={17} /> Search chats
        </button>
      </nav>
      <div className="mt-4 flex-1 overflow-y-auto px-2 text-sm">
        {threads.length === 0 && <div className="px-2.5 text-xs text-fg-subtle">No chats yet</div>}
        {[...groups.entries()].map(([g, items]) => (
          <div key={g} className="mb-4">
            <div className="px-2.5 pb-1 text-xs font-medium text-fg-subtle">{g}</div>
            {items.map((t) => (
              <div key={t.id} className={clsx('group flex items-center rounded-lg pr-1', t.id === activeId ? 'bg-hover' : 'hover:bg-hover')}>
                <button type="button" onClick={() => onSelect(t.id)} className="min-w-0 flex-1 truncate px-2.5 py-2 text-left">
                  {t.title}
                </button>
                <IconButton label="Delete chat" onClick={() => onDelete(t.id)} className="h-7 w-7 opacity-0 group-hover:opacity-100">
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
