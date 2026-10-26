import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { ArrowDown } from 'lucide-react';
import clsx from 'clsx';
import { useChat } from '../lib/useChat';
import { MockAnalystAdapter } from '../adapters/mock';
import { HttpAnalystAdapter } from '../adapters/http';
import { HttpTranscriptStore } from '../adapters/httpStore';
import { TemporaryAwareStore } from './temporaryStore';
import { AdapterCtx } from '../adapters/context';
import type { AnalystAdapter, TranscriptStore } from '../adapters/types';
import { Sidebar, SidebarRail } from '../components/Sidebar';
import { Composer } from '../components/Composer';
import { messageMarkdown, type Artifact } from '../components/Message';
import { artifactVersions, autoOpenIds, nextAutoOpen, userIsTyping } from '../components/artifacts';
import { ThreadView, type ThreadActions } from './ThreadView';
import { ArtifactPanel } from '../components/ArtifactPanel';
import { StarterCards } from './StarterCards';
import { Header } from './Header';
import { useStrategy } from './useStrategy';
import { setFeedbackCapabilities, setFeedbackSink, toFeedbackRequest } from '../lib/feedback';
import { OfflineBanner } from './OfflineBanner';
import { useOnline, useSettledOnline } from '../lib/useOnline';
import { usePrinting } from '../lib/usePrinting';
import { SearchDialog } from './SearchDialog';
import { useEdgeSwipeToOpen, useSwipeToClose } from './swipe';
import { ShareDialog, type ShareLinkApi } from './ShareDialog';
import { SettingsDialog, ShortcutsDialog } from './SettingsDialog';
import { ToastProvider, useFocusTrap, useToast } from './primitives';
import { NEW_CHAT_KEY, moveDraft } from './drafts';
import { transcriptMarkdown, exportFilename } from './exportMarkdown';
import type { SlashCommand } from './slash';
import { useTheme } from '../lib/theme';
import { scrollBehavior } from '../lib/motion';
import { copyText, downloadText } from '../lib/csv';
import { getPref, setPref } from '../lib/prefs';
import { sessionFromEnv, signOut } from '../auth/session';
import { signedOutUrl } from './routes';
import { trackInputModality } from '../lib/inputModality';
import type { AssistantView } from '../lib/contract';
import type { AssistantViewV1, ChatRequest } from '../api/contract';

/** One auth session for both adapters (src/auth/session.ts): demo token, real-backend cookie, or none. */
const session = sessionFromEnv();

export function makeAdapter(): AnalystAdapter {
  const base = import.meta.env.VITE_ANALYST_BASE as string | undefined;
  // VITE_ANALYST_BASE: '' or unset = offline mock; '/' = same origin (vite proxies /api to :8787); else an absolute URL.
  if (base === undefined || base === '') return new MockAnalystAdapter();
  return new HttpAnalystAdapter(base === '/' ? '' : base.replace(/\/$/, ''), session, demoFaultOption());
}

/**
 * Dev-only fault injection: `?fault=429` makes the first try of each answer come back 429 + Retry-After
 * from the demo API (which ignores the header outside demo mode), to exercise the auto-retry countdown.
 * Only in `vite dev` or a build made with VITE_DEMO_FAULTS=1; production builds drop this branch.
 */
function demoFaultOption(): { demoFault?: 'rate_limited' } {
  if (!(import.meta.env.DEV || import.meta.env.VITE_DEMO_FAULTS === '1')) return {};
  return new URLSearchParams(window.location.search).get('fault') === '429' ? { demoFault: 'rate_limited' } : {};
}

/**
 * Live API -> chat history comes from the server (/api/conversations, platform #104); offline mock -> memory.
 * VITE_TRANSCRIPTS=memory forces the in-memory store even against the live API.
 */
function makeStore(adapter: AnalystAdapter): TranscriptStore {
  const base = import.meta.env.VITE_ANALYST_BASE as string | undefined;
  if (adapter.name !== 'http' || import.meta.env.VITE_TRANSCRIPTS === 'memory' || base === undefined) return new TemporaryAwareStore();
  return new HttpTranscriptStore(base === '/' ? '' : base.replace(/\/$/, ''), session);
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

const focusComposer = () => document.querySelector<HTMLTextAreaElement>('textarea[aria-label="Message"]')?.focus();
/** Touch-first device: don't raise the on-screen keyboard unasked. */
const coarse = () => !!window.matchMedia?.('(pointer: coarse)').matches;

function ShellInner() {
  const auditOn = typeof window !== 'undefined' && new URLSearchParams(window.location.search).has('audit');
  const renderStart = useRef(0);
  // oxlint-disable-next-line react/purity, react/refs -- ?audit render profiling only: the render's own start time is the point
  if (auditOn) renderStart.current = performance.now();
  const adapter = useMemo(() => makeAdapter(), []);
  const store = useMemo(() => makeStore(adapter), [adapter]);
  const loadCaps = useMemo(() => (adapter instanceof HttpAnalystAdapter ? () => adapter.capabilities() : undefined), [adapter]);
  const strategy = useStrategy(loadCaps);
  const chat = useChat(adapter, store, strategy.options);
  // Contract 1.7.0 (§13): thumbs, reason ids and the comment go to the server for server-known answers
  // (never our 'a_' placeholders). Reason chips follow the server's Capabilities.feedbackReasons.
  const convRef = useRef(chat.convId);
  useLayoutEffect(() => { convRef.current = chat.convId; });
  useEffect(() => { setFeedbackCapabilities(strategy.caps); }, [strategy.caps]);
  useEffect(() => {
    if (!(adapter instanceof HttpAnalystAdapter)) return;
    setFeedbackSink(async (f) => {
      const cid = convRef.current;
      if (!cid || f.messageId.startsWith('a_')) return;
      await adapter.feedback(cid, f.messageId, toFeedbackRequest(f));
    });
    return () => setFeedbackSink(null);
  }, [adapter]);
  // Opening a stored chat can take a round trip (server history): after a short grace period show a
  // thread skeleton instead of the empty state or the previous chat (ChatGPT/Claude behaviour).
  const [opening, setOpening] = useState<string | null>(() => (store instanceof HttpTranscriptStore ? routeId() : null));
  // ---- temporary chat (QA-S22): memory store only; not in the sidebar, search or the URL; dropped on leave ----
  const tempStore = store instanceof TemporaryAwareStore ? store : null;
  const [temporary, setTemporaryState] = useState(false);
  const isTempChat = !!tempStore?.isTemporary(chat.convId);
  const setTemporary = (on: boolean) => {
    if (!tempStore) return;
    const leaving = !on && (isTempChat || chat.messages.length > 0);
    tempStore.temporary = on;
    setTemporaryState(on);
    if (leaving) chat.newChat();
    if (!on) void tempStore.discardTemporary();
  };
  const openChat = async (id: string) => {
    if (temporary) setTemporary(false);
    const t = setTimeout(() => setOpening(id), 120);
    try { await chat.open(id); } finally { clearTimeout(t); setOpening(null); }
  };
  // Server store: re-render once the history mode is known; reopen a routed chat after reload (ids only in the URL).
  const [, setStoreReady] = useState(false);
  const bootRoute = useRef(routeId());
  useEffect(() => {
    if (!(store instanceof HttpTranscriptStore)) return;
    store.ready.then(() => {
      setStoreReady(true);
      const id = bootRoute.current;
      if (id) void openChat(id); else setOpening(null);
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

  const [dialog, setDialog] = useState<null | 'search' | 'settings' | 'shortcuts' | 'share'>(null);
  const [enterToSend, setEnterToSendState] = useState(() => getPref('enterToSend') !== '0');
  const setEnterToSend = (v: boolean) => { setEnterToSendState(v); setPref('enterToSend', v ? '1' : '0'); };

  // ---- delete with undo: hide now, really delete when the toast expires ----
  const [pendingDelete, setPendingDelete] = useState<Set<string>>(new Set());
  const allThreads = chat.threads.filter((t) => !pendingDelete.has(t.id));
  const threads = allThreads.filter((t) => !t.archived);
  const archivedThreads = allThreads.filter((t) => t.archived);
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

  /** ChatGPT-style archive: the chat leaves the sidebar (still searchable); Undo or Settings → Archived chats restores it. */
  const requestArchive = useCallback((id: string) => {
    const title = chat.threads.find((t) => t.id === id)?.title ?? 'Chat';
    const wasOpen = chat.convId === id;
    void chat.archive(id, true);
    if (wasOpen) { chat.newChat(); requestAnimationFrame(focusComposer); }
    toast({
      text: `Archived “${title.length > 32 ? `${title.slice(0, 32)}…` : title}”`,
      ms: UNDO_MS,
      action: { label: 'Undo', run: () => { void chat.archive(id, false); if (wasOpen) void chat.open(id); } },
    });
  }, [chat, toast]);
  const unarchive = useCallback((id: string) => { void chat.archive(id, false); }, [chat]);

  // ---- routing (#/c/<id>) ----
  const routing = useRef(false);
  useEffect(() => {
    if (routing.current) return;
    const want = chat.convId && !tempStore?.isTemporary(chat.convId) ? `#/c/${chat.convId}` : '';
    if ((window.location.hash || '') !== want) {
      const url = want || window.location.pathname + window.location.search;
      if (chat.convId && routeId() === null && window.location.hash === '') window.history.replaceState(null, '', url);
      else window.history.pushState(null, '', url);
    }
  }, [chat.convId, tempStore]);
  useEffect(() => {
    const on = async () => {
      const id = routeId();
      if (id === chat.convId) return;
      routing.current = true;
      if (id && chat.threads.some((t) => t.id === id)) await openChat(id);
      else chat.newChat();
      routing.current = false;
    };
    window.addEventListener('popstate', on);
    window.addEventListener('hashchange', on);
    return () => { window.removeEventListener('popstate', on); window.removeEventListener('hashchange', on); };
    // oxlint-disable-next-line react-hooks/exhaustive-deps -- openChat only wraps chat.open (covered by `chat`) with the skeleton timer
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
  // Contract 1.8.0 §15: the server's own export, when the chat is stored server-side.
  const serverExport = adapter instanceof HttpAnalystAdapter && chat.convId && !chat.busy
    ? async (format: 'json' | 'markdown') => {
      try {
        const { blob, filename } = await adapter.exportConversation(chat.convId!, format);
        const url = URL.createObjectURL(blob);
        const a = Object.assign(document.createElement('a'), { href: url, download: filename });
        a.click();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
        toast({ text: format === 'json' ? 'Chat exported as JSON' : 'Chat exported as Markdown' });
      } catch {
        toast({ text: "Couldn't export this chat. Try again." });
      }
    }
    : undefined;
  // Contract 1.11.0 §17: org-only read-only link. Stable per chat, so the dialog fetches the link state once.
  const shareConv = adapter instanceof HttpAnalystAdapter && chat.convId && !chat.busy ? chat.convId : null;
  const shareLink = useMemo<ShareLinkApi | undefined>(() => (shareConv && adapter instanceof HttpAnalystAdapter ? {
    get: () => adapter.getShare(shareConv),
    set: (includeData, days) => adapter.setShare(shareConv, includeData, days),
    revoke: () => adapter.revokeShare(shareConv),
    copy: (url) => copyText(url),
  } : undefined), [adapter, shareConv]);
  const copyCurrent = async () => { if (await copyText(transcriptMarkdown(currentTitle, chat.messages))) toast({ text: 'Copied chat as Markdown' }); };
  const exportThread = async (id: string) => {
    const c = await store.getConversation(id);
    const title = threads.find((t) => t.id === id)?.title ?? 'Chat';
    if (c) downloadMd(title, transcriptMarkdown(title, c.messages));
  };
  const exportAll = async () => {
    const parts: string[] = [];
    for (const t of threads) { const c = await store.getConversation(t.id); if (c) parts.push(transcriptMarkdown(t.title, c.messages)); }
    downloadMd('claimclarity-chats', parts.join('\n---\n\n'));
  };
  const copyLast = async () => {
    const last = [...chat.messages].reverse().find((m): m is AssistantViewV1 => m.role === 'assistant');
    if (last && (await copyText(messageMarkdown(last as unknown as AssistantView)))) toast({ text: 'Copied last response' });
  };
  /** Touch-first devices: don't focus the composer on our own (it pops the on-screen keyboard), like the ChatGPT/Claude apps. */
  /**
   * After opening a chat from the sidebar or search, a desktop user can type the follow-up right away
   * (ChatGPT/Claude). Not on touch (no surprise keyboard), and never steal focus from another field the
   * user has moved to while the history loaded.
   */
  const focusAfterOpen = () => {
    if (coarse()) return;
    requestAnimationFrame(() => {
      const a = document.activeElement as HTMLElement | null;
      if (a && a !== document.body && a.matches('input, textarea, select, [contenteditable="true"]') && !a.matches('textarea[aria-label="Message"]')) return;
      focusComposer();
    });
  };

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
  useEffect(() => trackInputModality(), []);
  const onKeys = (e: KeyboardEvent) => {
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
    // Esc stops a streaming reply (Claude) when focus is in the composer or nowhere in particular.
    else if (k === 'escape' && chat.busy && !e.defaultPrevented && !dialog && (document.activeElement === document.body || document.activeElement?.closest('[data-testid="composer"]'))) { e.preventDefault(); chat.stop(); }
  };
  useLayoutEffect(() => { keys.current = onKeys; });
  useEffect(() => {
    const on = (e: KeyboardEvent) => keys.current(e);
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, []);

  const drawerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(drawerRef, mobile && drawerOpen);
  const swipeRef = useRef<HTMLDivElement>(null);
  const dismissDrawer = useCallback(() => setDrawerOpen(false), []);
  const openDrawer = useCallback(() => setDrawerOpen(true), []);
  useSwipeToClose(swipeRef, mobile && drawerOpen, dismissDrawer);
  useEdgeSwipeToOpen(mobile && !drawerOpen && !dialog, openDrawer, 16);

  // ---- artifact panel ----
  // `follow`: the panel tracks the highest version of a versioned artifact (contract 1.9 §16), so a v2 that
  // arrives while v1 is open replaces it; picking an older version chip turns following off.
  const [artifactSel, setArtifact] = useState<(Artifact & { follow?: boolean }) | null>(null);
  const artifactVersionList = artifactSel?.artifactId ? artifactVersions(chat.messages, artifactSel.artifactId) : undefined;
  const artifact: Artifact | null = !artifactSel ? null
    : artifactVersionList?.length ? (artifactSel.follow ? artifactVersionList[artifactVersionList.length - 1] : artifactVersionList.find((v) => v.version === artifactSel.version) ?? artifactSel)
      : artifactSel;
  // Opening an artifact on a narrower desktop makes room by collapsing the sidebar (an event, not an effect).
  const openArtifact = (a: Artifact | null) => {
    if (a?.artifactId) {
      const vs = artifactVersions(chat.messages, a.artifactId);
      const top = vs[vs.length - 1]?.version ?? a.version;
      setArtifact({ ...a, follow: a.version === undefined || a.version >= (top ?? 0) });
    } else setArtifact(a);
    if (a && window.innerWidth < 1400 && !mobile) setSidebar(false);
  };
  // autoOpen (§16): open once when an artifact part first arrives live, never on reload and never while typing
  // (and never move focus). Only messages seen streaming in this tab count as live.
  // A burst can deliver the artifact and message.end in one chunk (never rendered as streaming), and the server
  // message id replaces the client placeholder, so "busy at the previous run" also marks the last answer live.
  const liveMsgs = useRef(new Set<string>());
  const autoSeen = useRef(new Set<string>());
  const busyBefore = useRef(false);
  useEffect(() => {
    const lastAnswer = [...chat.messages].reverse().find((m) => m.role === 'assistant');
    if (lastAnswer && (busyBefore.current || chat.busy)) liveMsgs.current.add(lastAnswer.id);
    busyBefore.current = chat.busy;
    const live = chat.messages.filter((m) => m.role === 'assistant' && ((m as { streaming?: boolean }).streaming || liveMsgs.current.has(m.id)));
    for (const m of live) liveMsgs.current.add(m.id);
    // Answers already there when a chat loads (reload, history, version switch) never auto-open later.
    for (const id of autoOpenIds(chat.messages.filter((m) => !live.includes(m)))) autoSeen.current.add(id);
    if (!live.length) return;
    const next = nextAutoOpen(live, autoSeen.current);
    for (const id of autoOpenIds(live)) autoSeen.current.add(id);
    if (!next || userIsTyping()) return;
    const { partId: _p, ...a } = next;
    openArtifact({ ...a });
  }, [chat.messages, chat.busy]); // eslint-disable-line react-hooks/exhaustive-deps

  // ---- scrolling: pin each new question (the first one too, QA-S3) to the top while its answer streams ----
  const [draft, setDraftPush] = useState<{ text: string; n: number } | undefined>();
  const scroller = useRef<HTMLDivElement>(null);
  const content = useRef<HTMLDivElement>(null);
  const scrollToIndexRef = useRef<((index: number) => void) | null>(null);
  const [atBottom, setAtBottom] = useState(true);
  const atBottomRef = useRef(true);
  const pinned = useRef(false);
  const lastLen = useRef(0);
  const { messages } = chat;
  const messagesRef = useRef(messages);
  const busyRef = useRef(chat.busy);
  useLayoutEffect(() => { messagesRef.current = messages; busyRef.current = chat.busy; });
  useEffect(() => {
    const el = scroller.current;
    if (!el) return;
    const grew = messages.length > lastLen.current;
    lastLen.current = messages.length;
    const last = messages[messages.length - 1];
    const prev = messages[messages.length - 2];
    if (grew && last?.role === 'assistant' && prev?.role === 'user') {
      pinned.current = true;
      // A virtual window unmounts the streaming row if we animate the scroll. Jump by index instead.
      if (scrollToIndexRef.current) {
        scrollToIndexRef.current(messages.length - 2);
        return;
      }
      const users = el.querySelectorAll<HTMLElement>('[data-testid="msg-user"]');
      const u = users[users.length - 1];
      if (u) {
        const top = el.scrollTop + u.getBoundingClientRect().top - el.getBoundingClientRect().top - 16;
        if (typeof el.scrollTo === 'function') el.scrollTo({ top, behavior: scrollBehavior() });
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
    const ro = new ResizeObserver(() => {
      if (atBottomRef.current && !pinned.current) el.scrollTop = el.scrollHeight;
      // QA-A2: a pinned question lets the answer grow below the fold (chart under the composer)
      // without any scroll event. Re-measure so "Jump to latest" shows, like ChatGPT's down arrow.
      else setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80);
    });
    ro.observe(inner);
    return () => ro.disconnect();
  }, [messages.length === 0]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { pinned.current = false; lastLen.current = 0; atBottomRef.current = true; }, [chat.convId]);
  // Only a scroll the reader made can release the pin (QA-S12). While pinned, the reserved space under the
  // question leaves the view "at the bottom", so a programmatic scroll or a layout clamp must not count.
  const userScrollAt = useRef(0);
  // Direction of the reader's last wheel: an upward wheel always means "stop following", even when it lands
  // during the pin's smooth scroll and moves nothing (which used to read as "at bottom" and release the pin
  // into follow mode, yanking the view down once the answer outgrew the reserve).
  const wheelDir = useRef(0);
  const jumping = useRef(false);
  const markUserScroll = (e?: { deltaY?: number }) => { userScrollAt.current = Date.now(); wheelDir.current = Math.sign(e?.deltaY ?? 0); };
  const onScroll = (el: HTMLDivElement) => {
    const b = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    const byUser = Date.now() - userScrollAt.current < 800;
    if (byUser && wheelDir.current < 0) { jumping.current = false; atBottomRef.current = false; setAtBottom(b); return; }
    // "Jump to latest" smooth-scrolls; its intermediate positions must not read as the reader leaving the bottom.
    if (jumping.current) { if (b) jumping.current = false; else { atBottomRef.current = true; return; } }
    if (pinned.current && !byUser) { setAtBottom(b); return; }
    atBottomRef.current = b;
    if (b) pinned.current = false;
    setAtBottom(b);
  };
  const jumpToLatest = () => {
    pinned.current = false;
    atBottomRef.current = true;
    jumping.current = true;
    setTimeout(() => { jumping.current = false; }, 1000);
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: scrollBehavior() });
  };

  // ---- screen readers: one polite status region (QA-S1, QA-S16): "answering" → one excerpt → "Answer complete" ----
  const [announce, setAnnounce] = useState('');
  const wasBusy = useRef(false);
  useEffect(() => {
    if (chat.busy && !wasBusy.current) setAnnounce('ClaimClarity is answering');
    if (!chat.busy && wasBusy.current) {
      const last = messages[messages.length - 1];
      setAnnounce(last?.role === 'assistant' && last.parts.some((p) => p.type === 'error') ? 'Answer failed' : 'Answer complete');
    }
    wasBusy.current = chat.busy;
  }, [chat.busy]); // eslint-disable-line react-hooks/exhaustive-deps
  // Per-chat view state resets while rendering the new chat (React's "adjust state on prop change"), not in an effect.
  const [viewConv, setViewConv] = useState(chat.convId);
  if (viewConv !== chat.convId) {
    setViewConv(chat.convId);
    setArtifact(null);
    setAtBottom(true);
    setAnnounce('');
  }
  // QA-S16: the thread itself is NOT a live region (it announced every token, timer tick, table cell and
  // axis label). The one polite status region says "answering", then ONE short excerpt of the answer's
  // opening once there's a sentence to read, then "Answer complete". Same for short and virtualized threads.
  useEffect(() => {
    if (!chat.busy) return;
    let timer = 0;
    let cancelled = false;
    const tick = () => {
      timer = window.setTimeout(() => {
        if (cancelled || !busyRef.current) return;
        const last = messagesRef.current[messagesRef.current.length - 1];
        // message.end already arrived (a table may still be revealing): "Answer complete" is next, skip the excerpt.
        if (last?.role === 'assistant' && !(last as { streaming?: boolean }).streaming) return;
        const text = last?.role === 'assistant' ? last.parts.flatMap((p) => (p.type === 'text' ? [p.text] : [])).join(' ').replace(/[*_`#>|]+/g, '').replace(/\s+/g, ' ').trim() : '';
        const sentence = text.match(/^.{20,240}?[.!?](\s|$)/)?.[0] ?? (text.length >= 240 ? `${text.slice(0, 240)}…` : '');
        // flushSync: commit now, so the excerpt can't be batched into the same paint as "Answer complete".
        if (sentence) flushSync(() => setAnnounce(`ClaimClarity is answering. ${sentence.trim()}`));
        else tick();
      }, 1200);
    };
    tick();
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [chat.busy]);

  /** Deadline (ms) during which a send keeps typing focus: any commit that leaves focus on <body> (the centred
   *  composer unmounting, the docked one mounting, the first answer rows) puts it back in the composer. It never
   *  steals focus from an element the user picked, so it is safe to leave armed for the whole window. */
  const refocusUntil = useRef(0);
  const refocusIfLost = () => {
    if (performance.now() > refocusUntil.current) return;
    const a = document.activeElement;
    if (!a || a === document.body) focusComposer();
  };
  // Runs after every commit; a no-op unless a send is waiting to get focus back.
  useLayoutEffect(() => { refocusIfLost(); });
  const edit = (t: string) => setDraftPush((d) => ({ text: t, n: (d?.n ?? 0) + 1 }));
  const empty = messages.length === 0 && !opening;
  const draftKey = chat.convId ?? NEW_CHAT_KEY;
  const send = (t: string, resume?: ChatRequest['resume']) => {
    // The first send swaps the centered empty-state composer for the docked one (a remount), which used to
    // drop focus to <body>. Keep typing focus where it was, like ChatGPT/Claude: from the composer, or from a
    // starter card on a fine pointer (a phone keeps its keyboard closed after tapping a card).
    const a = document.activeElement;
    const keep = a?.matches('textarea[aria-label="Message"]') || (!!a?.closest('[data-testid="starter-cards"]') && !coarse());
    atBottomRef.current = true; setAtBottom(true); chat.send(t, resume ? { resume } : undefined);
    if (!keep) return;
    // QA-S19: Firefox can run this frame before React commits the docked composer, so the refocus also runs
    // in a layout effect after the commit (below); this frame is the backstop for a send that doesn't remount.
    refocusUntil.current = performance.now() + 1500;
    requestAnimationFrame(refocusIfLost);
  };
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
  const online = useOnline(); // gates Send at once
  const bannerOnline = useSettledOnline(); // the banner waits out sub-second flaps
  const printing = usePrinting();
  const composer = (autoFocus: boolean, placeholder?: string) => (
    <>
      {!bannerOnline && <OfflineBanner />}
      <Composer onEditLast={editLast} onSend={send} onStop={chat.stop} busy={chat.busy} autoFocus={autoFocus} initial={draft} placeholder={placeholder} draftKey={draftKey} enterToSend={enterToSend} onCommand={onCommand} offline={!online} />
    </>
  );
  const threadActions: ThreadActions = {
    editAt: (i, t) => { atBottomRef.current = true; setAtBottom(true); chat.editAt(i, t); },
    switchVersion: chat.switchVersion,
    versionOf: chat.versionOf,
    openArtifact,
    regenerate: chat.regenerate,
    editIntoComposer: edit,
    send,
    // Clarification chips / free text resume the paused run (ChatRequest.resume); abstain rephrasings send plainly.
    reply: (t, resume) => send(t, resume),
  };
  useLayoutEffect(() => {
    if (!auditOn) return;
    const ms = performance.now() - renderStart.current;
    const stats = (window.__ccRenderStats ??= { commits: 0, actual: 0, worst: 0, children: 0 });
    stats.commits += 1;
    stats.actual += ms;
    stats.worst = Math.max(stats.worst, ms);
  });
  const searchStore = useCallback((q: string) => store.search?.(q) ?? Promise.resolve([]), [store]);
  const openFromSearch = async (id: string, messageId?: string) => {
    await openChat(id);
    closeDrawer();
    if (!messageId) focusAfterOpen();
    if (messageId) requestAnimationFrame(() => requestAnimationFrame(() => {
      const target = document.querySelector<HTMLElement>(`[data-message-id="${CSS.escape(messageId)}"]`);
      target?.scrollIntoView?.({ block: 'center' });
    }));
  };

  const sidebarProps = {
    threads, activeId: chat.convId, storeMode: store.mode, canPin: chat.canPin,
    onSelect: (id: string) => { closeDrawer(); void openChat(id).then(focusAfterOpen); },
    onNew: () => { if (isTempChat) void tempStore?.discardTemporary(); chat.newChat(); closeDrawer(); if (!coarse()) requestAnimationFrame(focusComposer); },
    onDelete: requestDelete, onRename: chat.rename, onPin: chat.pin, onExport: exportThread,
    onArchive: chat.canArchive ? requestArchive : undefined,
    onCollapse: () => setSidebar(false), onSearch: () => setDialog('search'),
    onSettings: () => setDialog('settings'), onShortcuts: () => setDialog('shortcuts'),
    onSignOut: session.kind === 'none' ? undefined : () => {
      const base = import.meta.env.VITE_ANALYST_BASE as string | undefined;
      void signOut(!base || base === '/' ? '' : base.replace(/\/$/, ''), session, {
        signInUrl: (import.meta.env.VITE_SIGN_IN_URL as string | undefined) || signedOutUrl(import.meta.env.BASE_URL),
      });
    },
  };

  return (
    <AdapterCtx.Provider value={adapter}>
      <div className="flex h-full overflow-hidden bg-bg text-fg">
        <a href="#composer" onClick={(e) => { e.preventDefault(); focusComposer(); }} className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[80] focus:rounded-lg focus:bg-elev focus:px-3 focus:py-2 focus:shadow">Skip to message box</a>
        {mobile && drawerOpen && (
          <div ref={swipeRef} className="contents" data-testid="drawer-layer">
            <div className="fixed inset-0 z-30 cc-pan-y bg-black/40 motion-safe:animate-[cc-fade_150ms_ease-out]" aria-hidden onClick={dismissDrawer} data-testid="drawer-scrim" />
            <div ref={drawerRef}><Sidebar {...sidebarProps} drawer /></div>
          </div>
        )}
        {sidebarVisible && !mobile && <Sidebar {...sidebarProps} />}
        {/* Phone: a thin left-edge strip where a rightward swipe opens the drawer (the page itself keeps native panning). */}
        {mobile && !drawerOpen && !dialog && <div className="cc-pan-y fixed bottom-0 left-0 top-[52px] z-20 w-3" aria-hidden data-testid="drawer-edge" />}
        {!sidebarVisible && !mobile && <SidebarRail onExpand={() => setSidebar(true)} onNew={sidebarProps.onNew} onSearch={() => setDialog('search')} onSettings={() => setDialog('settings')} />}
        <main className="relative flex min-w-0 flex-1 flex-col font-sans md:min-w-[400px]" aria-label="Chat">
          {!empty && <p className="hidden pb-4 text-lg font-semibold print:block" data-testid="print-title">{currentTitle}</p>}
          <Header strategy={strategy} showNav={mobile} onOpenSidebar={() => setSidebar(true)} onNewChat={sidebarProps.onNew} adapterName={adapter.name}
            hasChat={!empty} onShare={isTempChat ? undefined : () => setDialog('share')}
            temporary={temporary} onTemporary={tempStore && (empty || temporary) ? setTemporary : undefined} onCopyMarkdown={copyCurrent} onDownloadMarkdown={exportCurrent} onSettings={() => setDialog('settings')} onShortcuts={() => setDialog('shortcuts')}
            title={chat.convId ? threads.find((t) => t.id === chat.convId)?.title : undefined}
            onRename={(t) => { if (chat.convId) chat.rename(chat.convId, t); }} onDelete={() => { if (chat.convId) requestDelete(chat.convId); }} />

          {empty ? (
            <div className="flex flex-1 flex-col items-center justify-center px-4 pb-[12vh] max-sm:justify-end max-sm:pb-[max(1rem,env(safe-area-inset-bottom))]" data-testid="empty-state">
              <h1 className="mb-7 text-center tracking-tight max-sm:mb-auto max-sm:mt-auto" style={{ fontFamily: 'var(--heading-font)', fontSize: 'var(--empty-size)', fontWeight: 'var(--empty-weight)' as never }}>{temporary ? 'Temporary chat' : 'What should we look into?'}</h1>
              {temporary && <p className="-mt-4 mb-7 max-w-[28rem] text-center text-sm text-fg-muted max-sm:-mt-2" data-testid="temporary-note">This chat won’t appear in your history or search, and it’s cleared when you leave it.</p>}
              <div className="flex w-full max-w-[48rem] flex-col" id="composer">
                {composer(!coarse())}
                <StarterCards onPick={(p) => send(p)} />
              </div>
            </div>
          ) : (
            <>
              {/* oxlint-disable-next-line jsx-a11y/no-noninteractive-element-interactions -- scroll-intent tracking on a focusable scroll region */}
              <div ref={scroller} tabIndex={0} role="region" aria-label="Conversation" onScroll={(e) => onScroll(e.currentTarget)} onWheel={(e) => markUserScroll(e)} onTouchMove={() => markUserScroll()} onPointerDown={() => markUserScroll()} onKeyDown={() => markUserScroll()} className="flex-1 overflow-y-auto [overflow-anchor:none] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-[var(--focus)]" data-testid="thread-scroller">
                <div ref={content} className="mx-auto flex max-w-[51rem] flex-col gap-6 px-4 pb-10 pt-4 md:px-6" data-testid="thread-content">
                  <h1 className="sr-only" data-testid="thread-title">{currentTitle}</h1>
                  {opening ? <ThreadSkeleton /> : <ThreadView messages={messages} busy={chat.busy} actions={threadActions} scrollRef={scroller} scrollToIndexRef={scrollToIndexRef} renderAll={printing} />}
                </div>
              </div>
              <div className="relative mx-auto w-full max-w-[51rem] px-4 md:px-6" id="composer">
                {/* Content scrolls under a soft fade instead of a hard edge above the composer. */}
                <div className="pointer-events-none absolute inset-x-0 -top-6 h-6 bg-linear-to-b from-transparent to-bg" aria-hidden />
                <button type="button" aria-label="Jump to latest" tabIndex={atBottom ? -1 : 0} aria-hidden={atBottom} onClick={jumpToLatest} data-testid="jump-latest"
                  className={clsx('absolute -top-12 left-1/2 z-10 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-bg text-fg-muted shadow-sm transition-[opacity,transform] duration-150 hover:text-fg',
                    atBottom ? 'pointer-events-none translate-y-2 opacity-0' : 'opacity-100')}>
                  <ArrowDown size={16} />
                </button>
                {composer(false, 'Ask a follow-up')}
                <p className="py-2 text-center text-xs text-fg-subtle">ClaimClarity can make mistakes. Check figures against source before use.</p>
              </div>
            </>
          )}
          <div className="sr-only" role="status" aria-live="polite" aria-atomic="true" data-testid="status-live">{announce}</div>
        </main>
        {artifact && <ArtifactPanel artifact={artifact} onClose={() => setArtifact(null)} versions={artifactVersionList}
          onVersion={(v) => { const vs = artifactVersionList ?? []; const a = vs.find((x) => x.version === v); if (a) setArtifact({ ...a, follow: v === vs[vs.length - 1]?.version }); }} />}
      </div>
      {dialog === 'search' && <SearchDialog threads={threads} search={searchStore} onOpen={openFromSearch} onNew={sidebarProps.onNew} onClose={() => setDialog(null)} />}
      {dialog === 'share' && (
        <ShareDialog title={currentTitle} firstQuestion={chat.messages.find((m) => m.role === 'user')?.text} messageCount={chat.messages.length}
          onClose={() => setDialog(null)} onCopyMarkdown={() => { void copyCurrent(); }} onDownloadMarkdown={exportCurrent}
          onDownloadJson={serverExport ? () => serverExport('json') : undefined} link={shareLink} expiry={strategy.caps.shareExpiry}
          onPrint={() => { setDialog(null); requestAnimationFrame(() => window.print()); }} />
      )}
      {dialog === 'settings' && <SettingsDialog onClose={() => setDialog(null)} storeMode={store.mode} enterToSend={enterToSend} setEnterToSend={setEnterToSend} onExportAll={exportAll} threadCount={allThreads.length}
        archived={chat.canArchive ? archivedThreads : undefined} onUnarchive={unarchive} onOpenChat={(id: string) => { setDialog(null); void openChat(id); }} />}
      {dialog === 'shortcuts' && <ShortcutsDialog onClose={() => setDialog(null)} />}
    </AdapterCtx.Provider>
  );
}

/** Placeholder thread while a stored chat loads: a question bubble and a few answer lines, shimmering. */
export function ThreadSkeleton() {
  return (
    <div className="flex flex-col gap-6" data-testid="thread-skeleton">
      <span className="sr-only" role="status">Loading chat…</span>
      {[0, 1].map((k) => (
        <div key={k} className="flex flex-col gap-6" aria-hidden>
          <div className="skeleton ml-auto h-11 w-[min(60%,22rem)] rounded-3xl" />
          <div className="flex flex-col gap-2.5">
            {[92, 98, 85, k ? 40 : 64].map((w, i) => <div key={i} className="skeleton h-3.5 rounded" style={{ width: `${w}%` }} />)}
          </div>
        </div>
      ))}
    </div>
  );
}
