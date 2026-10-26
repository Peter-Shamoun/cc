import { useEffect, useId, useMemo, useState } from 'react';
import { MessageSquare, Search, SquarePen } from 'lucide-react';
import clsx from 'clsx';
import { Dialog } from './primitives';
import type { SearchHit, ThreadSummary } from '../adapters/types';
import { groupThreads } from '../components/Sidebar';

type Row = { key: string; kind: 'new' } | { key: string; kind: 'thread'; hit: SearchHit | ThreadSummary; group?: string };

/**
 * Ctrl/⌘+K search (ChatGPT "Search chats" modal): full-text over titles and message bodies via
 * the transcript store, keyboard-first (combobox + listbox, aria-activedescendant).
 * The query stays in component state only: never in the URL, storage or logs.
 */
export function SearchDialog({ threads, search, onOpen, onNew, onClose }: { threads: ThreadSummary[]; search?: (q: string) => Promise<SearchHit[]>; onOpen: (id: string, messageId?: string) => void; onNew: () => void; onClose: () => void }) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<SearchHit[] | null>(null);
  const [active, setActive] = useState(0);
  const lid = useId();
  useEffect(() => {
    const query = q.trim();
    if (!query) { setHits(null); return; }
    let live = true;
    const id = window.setTimeout(async () => {
      const r = search ? await search(query) : threads.filter((t) => t.title.toLowerCase().includes(query.toLowerCase())).map((t) => ({ ...t, snippet: t.title, matchStart: t.title.toLowerCase().indexOf(query.toLowerCase()), matchLength: query.length }));
      if (live) { setHits(r); setActive(0); }
    }, 90);
    return () => { live = false; window.clearTimeout(id); };
  }, [q, search, threads]);
  const rows: Row[] = useMemo(() => {
    if (hits) return hits.map((h) => ({ key: `${h.id}:${h.messageId ?? 't'}`, kind: 'thread' as const, hit: h }));
    const out: Row[] = [{ key: 'new', kind: 'new' }];
    for (const [g, items] of groupThreads(threads)) items.slice(0, 50).forEach((t, i) => out.push({ key: t.id, kind: 'thread', hit: t, group: i === 0 ? g : undefined }));
    return out;
  }, [hits, threads]);
  const pick = (r: Row | undefined) => {
    if (!r) return;
    onClose();
    if (r.kind === 'new') onNew();
    else onOpen(r.hit.id, 'messageId' in r.hit ? r.hit.messageId : undefined);
  };
  useEffect(() => { document.getElementById(`${lid}-${active}`)?.scrollIntoView?.({ block: "nearest" }); }, [active, lid]);
  return (
    <Dialog title="Search chats" onClose={onClose} hideTitle className="max-w-[680px] overflow-hidden" testId="search-dialog">
      <div className="flex items-center gap-3 border-b border-line px-5 py-4">
        <Search size={18} className="shrink-0 text-fg-subtle" />
        <input autoFocus value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search chats…" aria-label="Search chats"
          role="combobox" aria-expanded="true" aria-controls={lid} aria-activedescendant={rows.length ? `${lid}-${active}` : undefined} aria-autocomplete="list"
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') { e.preventDefault(); setActive((a) => Math.min(rows.length - 1, a + 1)); }
            if (e.key === 'ArrowUp') { e.preventDefault(); setActive((a) => Math.max(0, a - 1)); }
            if (e.key === 'Enter') { e.preventDefault(); pick(rows[active]); }
          }}
          className="min-w-0 flex-1 bg-transparent text-base outline-none placeholder:text-fg-subtle focus-visible:outline-none" />
        <button type="button" onClick={onClose} className="shrink-0 rounded-md px-2 py-1 text-sm font-medium text-fg-muted hover:bg-hover hover:text-fg sm:hidden">Cancel</button>
      </div>
      <ul id={lid} role="listbox" aria-label="Results" className="scrollbar-thin max-h-[min(440px,60vh)] overflow-y-auto p-2 text-sm max-sm:max-h-none max-sm:flex-1">
        {hits && hits.length === 0 && <li className="px-3 py-8 text-center text-fg-subtle" role="presentation">No chats match “{q}”</li>}
        {rows.map((r, i) => (
          <li key={r.key} role="presentation">
            {r.kind === 'thread' && r.group && <div className="px-3 pb-1 pt-3 text-xs font-medium text-fg-subtle" role="presentation">{r.group}</div>}
            <div id={`${lid}-${i}`} role="option" aria-selected={i === active} onMouseMove={() => setActive(i)} onClick={() => pick(r)}
              className={clsx('flex cursor-pointer items-start gap-3 rounded-xl px-3 py-2.5', i === active && 'bg-hover')} data-testid="search-result">
              {r.kind === 'new' ? (<><SquarePen size={17} className="mt-0.5 shrink-0" /><span>New chat</span></>) : (
                <>
                  <MessageSquare size={17} className="mt-0.5 shrink-0 text-fg-muted" />
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-medium">{r.hit.title}</span>
                    {'snippet' in r.hit && r.hit.messageId && <Snippet hit={r.hit} />}
                  </span>
                </>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Dialog>
  );
}

function Snippet({ hit }: { hit: SearchHit }) {
  const { snippet: s, matchStart: a, matchLength: n } = hit;
  if (a < 0) return <span className="line-clamp-2 text-fg-muted">{s}</span>;
  return (
    <span className="line-clamp-2 text-fg-muted">
      {s.slice(0, a)}<mark className="rounded-sm bg-[#ffd84d]/45 px-0.5 text-fg dark:bg-[#ffd84d]/25">{s.slice(a, a + n)}</mark>{s.slice(a + n)}
    </span>
  );
}
