import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { ArrowDown } from 'lucide-react';
import clsx from 'clsx';
import { useChat } from '../lib/useChat';
import { MockAnalystAdapter } from '../adapters/mock';
import { MemoryTranscriptStore } from '../adapters/memoryStore';
import { HttpAnalystAdapter } from '../adapters/http';
import { HttpTranscriptStore } from '../adapters/httpStore';
import { AdapterCtx } from '../adapters/context';
import type { AnalystAdapter, TranscriptStore } from '../adapters/types';
import { Sidebar, SidebarRail } from '../components/Sidebar';
import { Composer } from '../components/Composer';
import { messageMarkdown, type Artifact } from '../components/Message';
import { ThreadView, type ThreadActions } from './ThreadView';
import { ArtifactPanel } from '../components/ArtifactPanel';
import { SUGGESTIONS } from '../fixtures';
import { Header } from './Header';
import { SearchDialog } from './SearchDialog';
import { SettingsDialog, ShortcutsDialog } from './SettingsDialog';
import { ToastProvider, useFocusTrap, useToast } from './primitives';
import { NEW_CHAT_KEY, moveDraft } from './drafts';
import { transcriptMarkdown, exportFilename } from './exportMarkdown';
import type { SlashCommand } from './slash';
import { useTheme } from '../lib/theme';
import { copyText, downloadText } from '../lib/csv';
import { getPref, setPref } from '../lib/prefs';
import type { AssistantView } from '../lib/contract';
import type { AssistantViewV1, ChatRequest } from '../api/contract';

function makeAdapter(): AnalystAdapter {
  const base = import.meta.env.VITE_ANALYST_BASE as string | undefined;
  // VITE_ANALYST_BASE: '' or unset = offline mock; '/' = same origin (vite proxies /api to :8787); else an absolute URL.
  if (base === undefined || base === '') return new MockAnalystAdapter();
  return new HttpAnalystAdapter(base === '/' ? '' : base.replace(/\/$/, ''), import.meta.env.VITE_ANALYST_DEMO_TOKEN as string | undefined);
}

/**
 * Live API -> chat history comes from the server (/api/conversations, platform #104); offline mock -> memory.
 * VITE_TRANSCRIPTS=memory forces the in-memory store even against the live API.
 */
function makeStore(adapter: AnalystAdapter): TranscriptStore {
  const base = import.meta.env.VITE_ANALYST_BASE as string | undefined;
  if (adapter.name !== 'http' || import.meta.env.VITE_TRANSCRIPTS === 'memory' || base === undefined) return new MemoryTranscriptStore();
  return new HttpTranscriptStore(base === '/' ? '' : base.replace(/\/$/, ''), import.meta.env.VITE_ANALYST_DEMO_TOKEN as string | undefined);
}

const phoneQuery = () => (typeof window.matchMedia === 'function' ? window.matchMedia('(max-width: 767px)') : null);
/** Hash routes carry only opaque ids (`#/c/c_x1y2`), never titles or text (no PHI in URLs, spec §6). */
const routeId = () => /^#\/c\/([\w-]+)$/.exec(window.location.hash)?.[1] ?? null;
const UNDO_MS = 5000;

export function AppShell() {
  return (
    <ToastProvider>
      <ShellInner />
    </ToastProvider>
  );
}

function ShellInner() {
  const adapter = useMemo(makeAdapter, []);
  const store = useMemo(() => makeStore(adapter), [adapter]);
  const chat = useChat(adapter, store);
  // Server store: re-render once the history mode is known; reopen a routed chat after reload (ids only in the URL).
  const [, setStoreReady] = useState(false);
  const bootRoute = useRef(routeId());
  useEffect(() => {
    if (!(store instanceof HttpTranscriptStore)) return;
    store.ready.then(() => {
      setStoreReady(true);
      const id = bootRoute.current;
      if (id) chat.open(id);
    });
  }, [store]); // eslint-disable-line react-hooks/exhaustive-deps
  const toast = useToast();
  const { setPreference } = useTheme();
  const [mobile, setMobile] = useState(() => phoneQuery()?.matches ?? false);
  // Desktop: sidebar open/collapsed is a remembered UI pref. Phone: a drawer, closed by default.
  const [desktopOpen, setDesktopOpen] = useState(() => getPref('sidebarCollapsed') !== '1');
  const [drawerOpen, setDrawerOpen] = useState(false);
  useEffect(() => {
    const mq = phoneQuery();
    if (!mq) return;
    const on = () => { setMobile(mq.matches); setDrawerOpen(false); };
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, []);
  const sidebarVisible = mobile ? drawerOpen : desktopOpen;
  const setSidebar = useCallback((open: boolean) => {
    if (mobile) setDrawerOpen(open);
    else { setDesktopOpen(open); setPref('sidebarCollapsed', open ? '0' : '1'); }
  }, [mobile]);
  const closeDrawer = () => { if (mobile) setDrawerOpen(false); };

  const [dialog, setDialog] = useState<null | 'search' | 'settings' | 'shortcuts'>(null);
  const [enterToSend, setEnterToSendState] = useState(() => getPref('enterToSend') !== '0');
  const setEnterToSend = (v: boolean) => { setEnterToSendState(v); setPref('enterToSend', v ? '1' : '0'); };

  // ---- delete with undo: hide now, really delete when the toast expires ----
  const [pendingDelete, setPendingDelete] = useState<Set<string>>(new Set());
  const threads = chat.threads.filter((t) => !pendingDelete.has(t.id));
  const requestDelete = useCallback((id: string) => {
    const title = chat.threads.find((t) => t.id === id)?.title ?? 'Chat';
    const wasOpen = chat.convId === id;
    // Keyboard users keep their place: focus moves to the next row (or the previous one, or the composer).
    const links = [...document.querySelectorAll<HTMLElement>('[data-thread-link]')];
    const at = links.findIndex((l) => l.dataset.threadLink === id);
    const nextId = (links[at + 1] ?? links[at - 1])?.dataset.threadLink;
    setPendingDelete((s) => new Set(s).add(id));
    if (wasOpen) chat.newChat();
    requestAnimationFrame(() => {
      const next = nextId && !wasOpen ? document.querySelector<HTMLElement>(`[data-thread-link="${CSS.escape(nextId)}"]`) : null;
      if (next) next.focus(); else focusComposer();
    });
    const forget = () => setPendingDelete((s) => { const n = new Set(s); n.delete(id); return n; });
    toast({
      text: `Deleted “${title.length > 32 ? `${title.slice(0, 32)}…` : title}”`,
      ms: UNDO_MS,
      // Undo puts the chat back where it was, and reopens it if it was the open one.
      action: { label: 'Undo', run: () => { forget(); if (wasOpen) void chat.open(id); } },
      onExpire: () => { chat.remove(id).then(forget); },
    });
  }, [chat, toast]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- routing (#/c/<id>) ----
  const routing = useRef(false);
  useEffect(() => {
    if (routing.current) return;
    const want = chat.convId ? `#/c/${chat.convId}` : '';
    if ((window.location.hash || '') !== want) {
      const url = want || window.location.pathname + window.location.search;
      if (chat.convId && routeId() === null && window.location.hash === '') window.history.replaceState(null, '', url);
      else window.history.pushState(null, '', url);
    }
  }, [chat.convId]);
  useEffect(() => {
    const on = async () => {
      const id = routeId();
      if (id === chat.convId) return;
      routing.current = true;
      if (id && chat.threads.some((t) => t.id === id)) await chat.open(id);
      else chat.newChat();
      routing.current = false;
    };
    window.addEventListener('popstate', on);
    window.addEventListener('hashchange', on);
    return () => { window.removeEventListener('popstate', on); window.removeEventListener('hashchange', on); };
  }, [chat]);
  // A route to a chat this tab doesn't hold (memory-only history, so: after reload) falls back to a new chat.
  useEffect(() => { if (routeId() && !chat.convId && !(store instanceof HttpTranscriptStore)) window.history.replaceState(null, '', window.location.pathname + window.location.search); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- drafts follow a new chat to its id ----
  const prevConv = useRef<string | null>(null);
  useEffect(() => {
    if (prevConv.current === null && chat.convId) moveDraft(NEW_CHAT_KEY, chat.convId);
    prevConv.current = chat.convId;
  }, [chat.convId]);

  // ---- export / copy ----
  const currentTitle = threads.find((t) => t.id === chat.convId)?.title ?? 'Chat';
  const downloadMd = (title: string, md: string) => { downloadText(exportFilename(title), md, 'text/markdown'); toast({ text: 'Chat exported as Markdown' }); };
  const exportCurrent = () => { if (chat.messages.length) downloadMd(currentTitle, transcriptMarkdown(currentTitle, chat.messages)); };
  const copyCurrent = async () => { if (await copyText(transcriptMarkdown(currentTitle, chat.messages))) toast({ text: 'Copied chat as Markdown' }); };
  const exportThread = async (id: string) => {
    const c = await store.getConversation(id);
    const title = threads.find((t) => t.id === id)?.title ?? 'Chat';
    if (c) downloadMd(title, transcriptMarkdown(title, c.messages));
  };
  const exportAll = async () => {
    const parts: string[] = [];
    for (const t of threads) { const c = await store.getConversation(t.id); if (c) parts.push(transcriptMarkdown(t.title, c.messages)); }
    downloadMd('cc-chats', parts.join('\n---\n\n'));
  };
  const copyLast = async () => {
    const last = [...chat.messages].reverse().find((m): m is AssistantViewV1 => m.role === 'assistant');
    if (last && (await copyText(messageMarkdown(last as unknown as AssistantView)))) toast({ text: 'Copied last response' });
  };
  const focusComposer = () => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]')?.focus();

  const onCommand = (c: SlashCommand) => {
    switch (c.action) {
      case 'new': chat.newChat(); break;
      case 'search': setDialog('search'); break;
      case 'export': exportCurrent(); break;
      case 'settings': setDialog('settings'); break;
      case 'shortcuts': setDialog('shortcuts'); break;
      case 'theme-light': setPreference('light'); break;
      case 'theme-dark': setPreference('dark'); break;
      case 'theme-system': setPreference('system'); break;
    }
  };

  // ---- global shortcuts (see shell/shortcuts.ts) ----
  const keys = useRef<(e: KeyboardEvent) => void>(() => {});
  keys.current = (e: KeyboardEvent) => {
    const mod = e.metaKey || e.ctrlKey;
    const k = e.key.toLowerCase();
    if (mod && !e.shiftKey && k === 'k') { e.preventDefault(); setDialog((d) => (d === 'search' ? null : 'search')); }
    else if (mod && e.shiftKey && k === 'o') { e.preventDefault(); chat.newChat(); closeDrawer(); requestAnimationFrame(focusComposer); }
    else if (mod && e.shiftKey && k === 's') { e.preventDefault(); setSidebar(!sidebarVisible); }
    else if (mod && e.shiftKey && k === 'c') { e.preventDefault(); copyLast(); }
    else if (mod && e.shiftKey && k === 'e') { e.preventDefault(); exportCurrent(); }
    else if (mod && (k === ',')) { e.preventDefault(); setDialog('settings'); }
    else if (mod && (k === '/' || k === '?')) { e.preventDefault(); setDialog((d) => (d === 'shortcuts' ? null : 'shortcuts')); }
    else if (e.shiftKey && k === 'escape') { e.preventDefault(); focusComposer(); }
    else if (k === 'escape' && mobile && drawerOpen) { setDrawerOpen(false); }
  };
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);

  const drawerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(drawerRef, mobile && drawerOpen);

  // ---- artifact panel ----
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  useEffect(() => { if (artifact && window.innerWidth < 1400 && !mobile) setSidebar(false); }, [artifact]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setArtifact(null), [chat.convId]);

  // ---- scrolling: pin each new question (the first one too, QA-S3) to the top while its answer streams ----
  const [draft, setDraftPush] = useState<{ text: string; n: number } | undefined>();
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const atBottomRef = useRef(true);
  const pinned = useRef(false);
  const lastLen = useRef(0);
  const { messages } = chat;
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const grew = messages.length > lastLen.current;
    lastLen.current = messages.length;
    const last = messages[messages.length - 1];
    const prev = messages[messages.length - 2];
    if (grew && last?.role === 'assistant' && prev?.role === 'user') {
      const users = el.querySelectorAll<HTMLElement>('[data-testid="msg-user"]');
      const u = users[users.length - 1];
      if (u) {
        pinned.current = true;
        const top = el.scrollTop + u.getBoundingClientRect().top - el.getBoundingClientRect().top - 16;
        if (typeof el.scrollTo === 'function') el.scrollTo({ top, behavior: window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ? 'auto' : 'smooth' });
        else el.scrollTop = top;
        return;
      }
    }
    if (atBottomRef.current && !pinned.current) el.scrollTop = el.scrollHeight;
  }, [messages]);
  // Late-growing content (charts, images, tables measured after paint) keeps the view anchored at the bottom.
  useEffect(() => {
    const el = scroller.current, inner = content.current;
    if (!el || !inner || typeof ResizeObserver === 'undefined') return;
    const ro = new ResizeObserver(() => { if (atBottomRef.current && !pinned.current) el.scrollTop = el.scrollHeight; });
    ro.observe(inner);
    return () => ro.disconnect();
  }, [messages.length === 0]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { pinned.current = false; lastLen.current = 0; atBottomRef.current = true; setAtBottom(true); }, [chat.convId]);
  const onScroll = (el: HTMLDivElement) => {
    const b = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    atBottomRef.current = b;
    if (b) pinned.current = false;
    setAtBottom(b);
  };
  const jumpToLatest = () => {
    pinned.current = false;
    atBottomRef.current = true;
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' });
  };

  // ---- screen readers: the log is aria-busy while streaming (one announcement, not per token), then "Answer complete" (QA-S1) ----
  const [announce, setAnnounce] = useState('');
  const wasBusy = useRef(false);
  useEffect(() => {
    if (chat.busy && !wasBusy.current) setAnnounce('CC is answering');
    if (!chat.busy && wasBusy.current) {
      const last = messages[messages.length - 1];
      setAnnounce(last?.role === 'assistant' && last.parts.some((p) => p.type === 'error') ? 'Answer failed' : 'Answer complete');
    }
    wasBusy.current = chat.busy;
  }, [chat.busy]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => setAnnounce(''), [chat.convId]);

  const edit = (t: string) => setDraftPush((d) => ({ text: t, n: (d?.n ?? 0) + 1 }));
  const empty = messages.length === 0;
  const draftKey = chat.convId ?? NEW_CHAT_KEY;
  const send = (t: string, resume?: ChatRequest['resume']) => { atBottomRef.current = true; setAtBottom(true); chat.send(t, resume ? { resume } : undefined); };
  /** Up-arrow in an empty composer edits the last question in place (ChatGPT behaviour, QA-S8). */
  const editLast = () => {
    const users = scroller.current?.querySelectorAll<HTMLElement>('[data-testid="msg-user"]');
    const btn = users?.[users.length - 1]?.querySelector<HTMLButtonElement>('button[aria-label="Edit message"]:not([disabled])');
    if (!btn) return false;
    btn.click();
    requestAnimationFrame(() => {
      const ta = scroller.current?.querySelector<HTMLTextAreaElement>('[data-testid="msg-user-editing"] textarea');
      if (ta) { ta.focus(); ta.setSelectionRange(ta.value.length, ta.value.length); ta.scrollIntoView?.({ block: 'nearest' }); }
    });
    return true;
  };
  const composer = (autoFocus: boolean, placeholder?: string) => (
    <Composer onEditLast={editLast} onSend={send} onStop={chat.stop} busy={chat.busy} autoFocus={autoFocus} initial={draft} placeholder={placeholder} draftKey={draftKey} enterToSend={enterToSend} onCommand={onCommand} />
  );
  const threadActions: ThreadActions = {
    editAt: (i, t) => { atBottomRef.current = true; setAtBottom(true); chat.editAt(i, t); },
    switchVersion: chat.switchVersion,
    versionOf: chat.versionOf,
    openArtifact: setArtifact,
    regenerate: chat.regenerate,
    editIntoComposer: edit,
    send,
    // Clarification chips / free text resume the paused run (ChatRequest.resume); abstain rephrasings send plainly.
    reply: (t, resume) => send(t, resume),
  };
  const searchStore = useCallback((q: string) => store.search?.(q) ?? Promise.resolve([]), [store]);
  const openFromSearch = async (id: string, messageId?: string) => {
    await chat.open(id);
    closeDrawer();
    if (messageId) requestAnimationFrame(() => requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(messageId)}"]`);
      target?.scrollIntoView?.({ block: 'center' });
    }));
  };

  const sidebarProps = {
    threads, activeId: chat.convId, storeMode: store.mode, canPin: chat.canPin,
    onSelect: (id: string) => { chat.open(id); closeDrawer(); },
    onNew: () => { chat.newChat(); closeDrawer(); requestAnimationFrame(focusComposer); },
    onDelete: requestDelete, onRename: chat.rename, onPin: chat.pin, onExport: exportThread,
    onCollapse: () => setSidebar(false), onSearch: () => setDialog('search'),
    onSettings: () => setDialog('settings'), onShortcuts: () => setDialog('shortcuts'),
  };

  return (
    <AdapterCtx.Provider value={adapter}>
      <div className="flex h-full overflow-hidden bg-bg text-fg">
        <a href="#composer" onClick={(e) => { e.preventDefault(); focusComposer(); }} className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[80] focus:rounded-lg focus:bg-elev focus:px-3 focus:py-2 focus:shadow">Skip to message box</a>
        {mobile && drawerOpen && <div className="fixed inset-0 z-30 bg-black/40 motion-safe:animate-[cc-fade_150ms_ease-out]" aria-hidden onClick={() => setDrawerOpen(false)} data-testid="drawer-scrim" />}
        {sidebarVisible && (mobile ? <div ref={drawerRef}><Sidebar {...sidebarProps} drawer /></div> : <Sidebar {...sidebarProps} />)}
        {!sidebarVisible && !mobile && <SidebarRail onExpand={() => setSidebar(true)} onNew={sidebarProps.onNew} onSearch={() => setDialog('search')} onSettings={() => setDialog('settings')} />}
        <main className="relative flex min-w-0 flex-1 flex-col font-sans md:min-w-[400px]" aria-label="Chat">
          <Header showNav={mobile} onOpenSidebar={() => setSidebar(true)} onNewChat={sidebarProps.onNew} adapterName={adapter.name}
            hasChat={!empty} onCopyMarkdown={copyCurrent} onDownloadMarkdown={exportCurrent} onSettings={() => setDialog('settings')} onShortcuts={() => setDialog('shortcuts')} />

          {empty ? (
            <div className="flex flex-1 flex-col items-center justify-center px-4 pb-[12vh] max-sm:justify-end max-sm:pb-[max(1rem,env(safe-area-inset-bottom))]" data-testid="empty-state">
              <h1 className="mb-7 text-center tracking-tight max-sm:mb-auto max-sm:mt-auto" style={{ fontFamily: 'var(--heading-font)', fontSize: 'var(--empty-size)', fontWeight: 'var(--empty-weight)' as never }}>What should we look into?</h1>
              <div className="flex w-full max-w-[48rem] flex-col" id="composer">
                {composer(true)}
                {/* Phones: one swipeable row above the composer with an edge fade (ChatGPT/Claude mobile), not a cut-off row at the screen edge. */}
                <div className="mt-4 flex flex-wrap justify-center gap-2 max-sm:order-first max-sm:-mx-4 max-sm:mb-3 max-sm:mt-0 max-sm:flex-nowrap max-sm:justify-start max-sm:overflow-x-auto max-sm:px-4 max-sm:pb-1 max-sm:scrollbar-none max-sm:[mask-image:linear-gradient(to_right,transparent,#000_1rem,#000_calc(100%-2rem),transparent)]" role="group" aria-label="Suggestions">
                  {SUGGESTIONS.map((s, i) => (
                    <button key={s.prompt} type="button" onClick={() => send(s.prompt)} style={{ animationDelay: `${i * 40}ms` }}
                      className="shrink-0 rounded-full border border-line bg-bg px-3.5 py-2 text-sm font-medium text-fg-muted transition-colors hover:bg-hover hover:text-fg motion-safe:animate-[cc-rise_240ms_ease-out_both]">{s.title}</button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <>
              <div ref={scroller} onScroll={(e) => onScroll(e.currentTarget)} className="flex-1 overflow-y-auto [overflow-anchor:none]" data-testid="thread-scroller">
                <div ref={content} className="mx-auto flex max-w-[48rem] flex-col gap-6 px-4 pb-10 pt-4 md:px-6" role="log" aria-live="polite" aria-relevant="additions" aria-busy={chat.busy} aria-label="Messages">
                  <h1 className="sr-only" data-testid="thread-title">{currentTitle}</h1>
                  <ThreadView messages={messages} busy={chat.busy} actions={threadActions} />
                </div>
              </div>
              <div className="relative mx-auto w-full max-w-[48rem] px-4 md:px-6" id="composer">
                {/* Content scrolls under a soft fade instead of a hard edge above the composer. */}
                <div className="pointer-events-none absolute inset-x-0 -top-6 h-6 bg-linear-to-b from-transparent to-bg" aria-hidden />
                <button type="button" aria-label="Jump to latest" tabIndex={atBottom ? -1 : 0} aria-hidden={atBottom} onClick={jumpToLatest} data-testid="jump-latest"
                  className={clsx('absolute -top-12 left-1/2 z-10 flex h-9 w-9 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-bg text-fg shadow-md transition-[opacity,transform] duration-200',
                    atBottom ? 'pointer-events-none translate-y-2 opacity-0' : 'opacity-100')}>
                  <ArrowDown size={16} />
                </button>
                {composer(false, 'Ask a follow-up')}
                <p className="py-2 text-center text-xs text-fg-subtle">CC can make mistakes. Check figures against source before use.</p>
              </div>
            </>
          )}
          <div className="sr-only" role="status" aria-live="polite" aria-atomic="true" data-testid="status-live">{announce}</div>
        </main>
        {artifact && <ArtifactPanel artifact={artifact} onClose={() => setArtifact(null)} />}
      </div>
      {dialog === 'search' && <SearchDialog threads={threads} search={searchStore} onOpen={openFromSearch} onNew={sidebarProps.onNew} onClose={() => setDialog(null)} />}
      {dialog === 'settings' && <SettingsDialog onClose={() => setDialog(null)} storeMode={store.mode} enterToSend={enterToSend} setEnterToSend={setEnterToSend} onExportAll={exportAll} threadCount={threads.length} />}
      {dialog === 'shortcuts' && <ShortcutsDialog onClose={() => setDialog(null)} />}
    </AdapterCtx.Provider>
  );
}
