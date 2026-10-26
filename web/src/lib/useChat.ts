import { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import type { AssistantMessage, ID, TablePart, UserMessage } from './contract';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type ChatRequest, type RegenerateStyle, type Usage } from '../api/contract';
import type { AnalystAdapter, ConnectionInfo, ConnectionState, ThreadSummary, TranscriptStore } from '../adapters/types';

const CONNECTION_LABEL = { offline: 'Offline, waiting for the network…', reconnecting: 'Reconnecting…' } as const;
const RATE_LIMIT_PREFIX = 'Busy right now';
/** Client transport statuses (offline, reconnecting, 429 countdown); these show even while the step timeline is live. */
export function isTransportStatus(s: string | null | undefined): boolean {
  return !!s && (s === CONNECTION_LABEL.offline || s === CONNECTION_LABEL.reconnecting || s.startsWith(RATE_LIMIT_PREFIX));
}
/**
 * What a screen reader should hear for a status change, or null to stay quiet. The visible 429 countdown
 * ticks every second; announcing each tick would talk over everything, so we speak when the wait starts,
 * every 5 s while it is long, and once when it retries. Other statuses are spoken when they change.
 */
export function transportAnnouncement(status: string | null | undefined, prev: string | null | undefined): string | null {
  if (!status || status === prev) return null;
  if (!status.startsWith(RATE_LIMIT_PREFIX)) return isTransportStatus(status) ? status.replace(/…$/, '') : null;
  const m = /retrying in (\d+)s/.exec(status);
  const wasCounting = !!prev && prev.startsWith(RATE_LIMIT_PREFIX) && /retrying in \d+s/.test(prev);
  if (!m) return wasCounting || !prev?.startsWith(RATE_LIMIT_PREFIX) ? 'Retrying now' : null;
  const n = Number(m[1]);
  const secs = `${n} second${n === 1 ? '' : 's'}`;
  if (!wasCounting) return prev?.startsWith(RATE_LIMIT_PREFIX) ? `Still busy. Retrying in ${secs}.` : `Busy right now. Retrying automatically in ${secs}.`;
  // Another 429 after the retry: the count jumps back up.
  if (n > Number(/retrying in (\d+)s/.exec(prev!)![1])) return `Still busy. Retrying in ${secs}.`;
  return n % 5 === 0 ? `Retrying in ${secs}.` : null;
}
/** Status line while a 429 is waited out: "Busy right now, retrying in 3s…" (never below 1s, then "retrying…"). */
export function rateLimitLabel(msLeft: number): string {
  const s = Math.ceil(msLeft / 1000);
  return s >= 1 ? `${RATE_LIMIT_PREFIX}, retrying in ${s}s…` : `${RATE_LIMIT_PREFIX}, retrying…`;
}
import { shortTitle } from '../shell/title';
import { frameBatch } from './frameBatch';
import { createTableReveal, prefersReducedMotion } from './tableReveal';
import { foldThread } from './threadTree';

const uid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 10)}`;
/** `uiKey`: a client-only render key fixed at send, so the row doesn't remount when message.start swaps in the server id (QA-A15: the Thinking row and its timer survive). */
export type ViewMessage = UserMessage | (AssistantViewV1 & { uiKey?: string; regenerateStyle?: RegenerateStyle; /** UI-only stub from withInterrupted. */ interrupted?: boolean });

export function toView(m: UserMessage | AssistantMessage): ViewMessage {
  if (m.role === 'user') return m;
  const v = m as AssistantMessage & Partial<AssistantViewV1>;
  return { ...v, parts: v.parts as AssistantViewV1['parts'], streaming: false, status: null, openText: [], steps: v.steps ?? [], escalations: v.escalations ?? [], lastSeq: -1 };
}

/**
 * A clarification that has a later turn after it was replied to (CONTRACT.md §6), so after a reload
 * it renders as answered instead of offering choices again. The server sets `answered` on its own
 * transcript; the memory store does not, so we derive it here too.
 */
/**
 * A stored chat that ends on the question has no answer: the page was reloaded or closed mid-answer and the server
 * cancelled the run after its disconnect grace (CONTRACT.md §8). Show that plainly with Retry (= regenerate, which
 * re-asks the same question without a second copy of it), never a question hanging with nothing under it.
 * The placeholder is UI-only ('a_' id, never stored) so Retry sends a fresh run, not `regenerate.messageId`.
 */
export function withInterrupted(msgs: ViewMessage[]): ViewMessage[] {
  const last = msgs[msgs.length - 1];
  if (!last || last.role !== 'user') return msgs;
  const id = uid('a');
  let v = newAssistantViewV1(id) as AssistantViewV1;
  v = reduceV1(v, { type: 'part', part: { type: 'error', id: uid('err'), code: 'internal', title: 'Response interrupted', message: 'This answer stopped before it finished. Retry to ask again.', retryable: true } } as never);
  v = reduceV1(v, { type: 'message.end', messageId: id, finishReason: 'error', usage: { queries: 0, elapsedMs: 0 } } as never);
  return [...msgs, { ...v, streaming: false, status: null, openText: [], uiKey: id, interrupted: true }];
}

export function markAnswered(msgs: ViewMessage[]): ViewMessage[] {
  return msgs.map((m, i) => {
    if (m.role !== 'assistant' || i === msgs.length - 1) return m;
    if (!m.parts.some((p) => p.type === 'clarification' && !p.answered)) return m;
    return { ...m, parts: m.parts.map((p) => (p.type === 'clarification' && !p.answered ? { ...p, answered: true } : p)) };
  });
}

/**
 * Usage for a run that ended without the server's `message.end` (Stop before it arrived, or a lost
 * connection): count the SQL that finished and the wall time, so the answer keeps its figures.
 */
export function partialUsage(view: AssistantViewV1, startedAt: number): Usage {
  if (view.usage) return view.usage;
  const queries = view.steps.filter((s) => s.kind === 'sql' && s.events.some((e) => e.type === 'step.sql_result')).length;
  return { queries, elapsedMs: Math.max(0, Date.now() - startedAt) };
}

/** Short client-side title (shell/title.ts) until the server returns a PHI-scrubbed one (spec §6.5). */
const draftTitle = (text: string) => shortTitle(text);

export function useChat(adapter: AnalystAdapter, store: TranscriptStore, getOptions?: () => ChatRequest['options'] | undefined) {
  // Read at send time (strategy/model picker) without re-creating every callback.
  const optionsRef = useRef(getOptions);
  useLayoutEffect(() => { optionsRef.current = getOptions; }, [getOptions]);
  const [threads, setThreads] = useState<ThreadSummary[]>([]);
  const [convId, setConvId] = useState<ID | null>(null);
  const [messages, setMessages] = useState<ViewMessage[]>([]);
  const [busy, setBusy] = useState(false);
  /**
   * Edit-and-branch (ChatGPT/Claude "< 2/3 >"): keyed by the index of the edited user message,
   * each version is the thread tail from that index on. Held in memory only, like the store.
   * Nested branches inside a non-visible version are flattened (v1 simplification).
   */
  const [branches, setBranches] = useState<Record<number, { versions: ViewMessage[][]; idx: number }>>({});
  const persist = (m: ViewMessage): UserMessage | AssistantMessage => {
    if (m.role === 'user') return m;
    // Steps, escalations, usage and finishReason stay with the message so a reopened chat
    // still shows its work timeline (memory store only; no PHI: steps carry no row values).
    const { streaming: _s, status: _st, openText: _o, lastSeq: _l, ...rest } = m;
    void _s; void _st; void _o; void _l;
    return rest as unknown as AssistantMessage;
  };
  const abortRef = useRef<AbortController | null>(null);
  /** Bumped by every run, open and newChat: a run that is no longer current must not touch the view. */
  const runRef = useRef(0);
  const messagesRef = useRef(messages);
  useLayoutEffect(() => { messagesRef.current = messages; }, [messages]);

  const refresh = useCallback(async () => setThreads((await store.listConversations()).items), [store]);
  useEffect(() => {
    // oxlint-disable-next-line react/set-state-in-effect -- loads the external transcript store; setState runs after the await
    refresh();
  }, [refresh]);

  const open = useCallback(async (id: ID) => {
    abortRef.current?.abort();
    runRef.current++;
    setBusy(false);
    const c = await store.getConversation(id);
    setConvId(c ? id : null);
    // Server transcripts are append-only with parentId links: show the newest path, keep edits as versions.
    const { visible, branches: b } = foldThread(c ? c.messages.map(toView) : []);
    setBranches(b);
    setMessages(withInterrupted(markAnswered(visible)));
  }, [store]);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    runRef.current++;
    setBusy(false);
    setConvId(null);
    setBranches({});
    setMessages([]);
  }, []);

  const run = useCallback(async (cid: ID, history: ViewMessage[], opts: { resume?: ChatRequest['resume']; regenerate?: ChatRequest['regenerate'] } = {}) => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    const placeholder = uid('a');
    let view: AssistantViewV1 & { uiKey?: string; regenerateStyle?: RegenerateStyle } = { ...newAssistantViewV1(placeholder), uiKey: placeholder };
    const startedAt = Date.now();
    history = markAnswered(history);
    const token = ++runRef.current;
    const current = () => runRef.current === token;
    // Stream batching (lib/frameBatch): one render per animation frame for text/reasoning deltas.
    // Partial-table streaming (lib/tableReveal): a table that arrives mid-answer grows over a few frames.
    const reveal = createTableReveal();
    // While a table is still growing the answer counts as streaming (no actions/footer yet), even if
    // message.end already arrived; the turn is released once it is fully shown.
    const shown = () => (reveal.active ? { ...view, streaming: true, parts: reveal.apply(view.parts) } : view);
    const batch = frameBatch(() => { if (current()) setMessages([...history, shown()]); });
    const set = batch.now;
    const schedule = batch.schedule;
    let drained: (() => void) | null = null;
    const revealLoop = frameBatch(() => {
      if (!current() || ctrl.signal.aborted) { reveal.finish(); drained?.(); return; }
      if (reveal.step()) revealLoop.schedule();
      else drained?.();
      batch.now(); // already inside a frame: paint this step (and any queued text) now
    });
    set();
    setBusy(true);
    const wire = history.map((m) => ({ id: m.id, role: m.role, text: m.role === 'user' ? m.text : m.parts.map((p) => (p.type === 'text' ? p.text : '')).join('\n'), ...(m.role === 'user' && m.parentId ? { parentId: m.parentId } : {}), ...(m.role === 'user' && m.editOf ? { editOf: m.editOf } : {}) }));
    // Transport hiccups show in the status line (client view state, not a contract event); the
    // server's own status comes back once the replay resumes.
    let serverStatus: string | null = null;
    let countdown: ReturnType<typeof setInterval> | undefined;
    const stopCountdown = () => { if (countdown !== undefined) { clearInterval(countdown); countdown = undefined; } };
    const isTransport = isTransportStatus;
    const onConnection = (state: ConnectionState, info?: ConnectionInfo) => {
      stopCountdown();
      if (!view.streaming || !current()) return;
      if (state === 'live') { view = { ...view, status: serverStatus }; set(); return; }
      if (!isTransport(view.status)) serverStatus = view.status ?? null;
      if (state === 'rate_limited') {
        // 429 + Retry-After: the adapter waits and retries on its own; count the wait down once a second.
        const until = Date.now() + (info?.retryInMs ?? 0);
        const tick = () => {
          if (!view.streaming || !current()) return stopCountdown();
          view = { ...view, status: rateLimitLabel(until - Date.now()) };
          set();
        };
        tick();
        countdown = setInterval(tick, 1000);
        return;
      }
      view = { ...view, status: CONNECTION_LABEL[state] };
      set();
    };
    let reloaded = false;
    try {
      for await (const ev of adapter.streamTurn({ conversationId: cid, messages: wire, signal: ctrl.signal, ...(opts.resume ? { resume: opts.resume } : {}), ...(opts.regenerate ? { regenerate: opts.regenerate } : {}), options: optionsRef.current?.(), onConnection })) {
        view = reduceV1(view, ev);
        if (ev.type === 'message.start') {
          const rs = (ev as { regenerateStyle?: RegenerateStyle }).regenerateStyle;
          if (rs && rs !== 'default') view = { ...view, regenerateStyle: rs };
        }
        if (ev.type === 'part' && ev.part.type === 'table' && !prefersReducedMotion() && reveal.note(view.parts) && !revealLoop.pending) revealLoop.schedule();
        // Text and reasoning deltas are batched; structural events (start, parts, steps, end) paint at once.
        if (ev.type === 'text.delta' || ev.type === 'reasoning.delta') schedule();
        else set();
        if (ev.type === 'message.end') break;
      }
    } catch (e) {
      // CONTRACT.md §8: the run finished (or expired) while we were away and the server can't replay it
      // (`stream_gone`). Reload the conversation and show the stored answer instead of "Connection lost".
      if ((e as Error).name === 'StreamGoneError' && !ctrl.signal.aborted) {
        const saved = await store.getConversation(cid).catch(() => undefined);
        const done = saved?.messages.find((m) => m.role === 'assistant' && m.id === view.id);
        if (done) { view = toView(done) as typeof view; reloaded = true; }
      }
      if ((e as Error).name !== 'AbortError' && !ctrl.signal.aborted && !reloaded) {
        view = reduceV1(view, { type: 'part', part: { type: 'error', id: uid('err'), code: 'internal', title: 'Connection lost', message: 'Response interrupted.', retryable: true } });
        if (view.streaming) view = reduceV1(view, { type: 'message.end', messageId: view.id, finishReason: 'error', usage: partialUsage(view, startedAt) } as never);
      }
    }
    stopCountdown();
    // A table that came in with the last events finishes growing (<= ~400 ms) before the turn is
    // released, so the next send/regenerate never starts from a half-shown table.
    if (reveal.active && current() && !ctrl.signal.aborted) {
      await new Promise<void>((resolve) => { drained = resolve; if (!revealLoop.pending) revealLoop.schedule(); setTimeout(resolve, 1000); });
    }
    reveal.finish();
    // Stopped before the server's aborted message.end arrived: keep what ran (queries, time) on the answer.
    if (ctrl.signal.aborted && view.streaming) view = reduceV1(view, { type: 'message.end', messageId: view.id, finishReason: 'aborted', usage: partialUsage(view, startedAt) } as never);
    view = { ...view, streaming: false, status: null, openText: [] };
    set();
    if (current()) setBusy(false);
    await store.appendMessage(cid, persist(view));
    refresh();
  }, [adapter, refresh, store]);

  /** Send a user turn. `resume` answers a clarification (CONTRACT.md §6) so the server continues that run. */
  // `busy` is React state: two calls in the same tick (double Enter, double-clicked Regenerate, Send while an
  // edit is starting) both see busy=false, and the store write awaits before run() sets it. This ref claims
  // the turn synchronously, so only the first starts.
  const startingRef = useRef(false);
  const claim = async (work: () => Promise<void>) => {
    if (startingRef.current) return;
    startingRef.current = true;
    try { await work(); } finally { startingRef.current = false; }
  };

  const send = useCallback(async (text: string, opts?: { resume?: ChatRequest['resume'] }) => {
    const t = text.trim();
    if (!t || busy || startingRef.current) return;
    await claim(async () => {
      const cid = convId ?? uid('c');
      if (!convId) setConvId(cid);
      const user: UserMessage = { id: uid('u'), role: 'user', text: t, createdAt: new Date().toISOString() };
      await store.appendMessage(cid, user, draftTitle(t));
      refresh(); // show the new chat in the sidebar now, not when the first answer ends (QA-S2)
      await run(cid, [...messages, user], { resume: opts?.resume });
    });
  }, [busy, convId, messages, refresh, run, store]);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const regenerate = useCallback((style: RegenerateStyle = 'default') => claim(async () => {
    if (!convId || busy) return;
    const lastUserIdx = messages.map((m) => m.role).lastIndexOf('user');
    if (lastUserIdx < 0) return;
    // A server-known answer (id from message.start, not our 'a_' placeholder) is re-run with
    // ChatRequest.regenerate (+ style, contract 1.10.0) so the server records a sibling instead of a second
    // copy of the question. No follow-up user turn is sent.
    const prev = messages.slice(lastUserIdx + 1).find((m) => m.role === 'assistant');
    const regen = prev && !prev.id.startsWith('a_') ? (style === 'default' ? { messageId: prev.id } : { messageId: prev.id, style }) : undefined;
    // The old answer stays reachable as a version (ChatGPT/Claude "‹ 2/2 ›"), same arrows as edits.
    // The "Response interrupted" stub is not an answer: Retry replaces it, it never becomes a version.
    if (prev && !('interrupted' in prev && prev.interrupted)) {
      const tail = messages.slice(lastUserIdx);
      const b = branches[lastUserIdx] ?? { versions: [tail], idx: 0 };
      const versions = [...b.versions];
      versions[b.idx] = tail;
      versions.push([messages[lastUserIdx]]);
      setBranches((all) => ({ ...Object.fromEntries(Object.entries(all).filter(([k]) => Number(k) < lastUserIdx)), [lastUserIdx]: { versions, idx: versions.length - 1 } }));
    }
    await store.truncateAfter(convId, messages[lastUserIdx].id);
    await run(convId, messages.slice(0, lastUserIdx + 1), { regenerate: regen });
  }), [branches, busy, convId, messages, run, store]);

  const remove = useCallback(async (id: ID) => {
    await store.deleteConversation(id);
    if (convId === id) newChat();
    refresh();
  }, [convId, newChat, refresh, store]);

  /** Edit user message `i`: keep the old tail as a version, run the edited question as a new one. */
  const editAt = useCallback((i: number, text: string) => claim(async () => {
    const t = text.trim();
    const old = messages[i];
    if (!convId || busy || !t || old?.role !== 'user' || t === old.text) return;
    const tail = messages.slice(i);
    const b = branches[i] ?? { versions: [tail], idx: 0 };
    const versions = [...b.versions];
    versions[b.idx] = tail;
    // parentId = the message before the edited one, editOf = the edited question (contract 1.2): the server
    // stores the edit as a sibling branch, and an edited first question as a new root (§8).
    const user: UserMessage = { id: uid('u'), role: 'user', text: t, createdAt: new Date().toISOString(), editOf: old.id, ...(i > 0 ? { parentId: messages[i - 1].id } : {}) };
    versions.push([user]);
    setBranches((all) => ({ ...Object.fromEntries(Object.entries(all).filter(([k]) => Number(k) < i)), [i]: { versions, idx: versions.length - 1 } }));
    const prefix = messages.slice(0, i);
    await store.replaceMessages(convId, [...prefix, user].map(persist));
    await run(convId, [...prefix, user]);
  }), [branches, busy, convId, messages, run, store]);

  /** Show version `k` of the branch at user message `i`. */
  const switchVersion = useCallback(async (i: number, k: number) => {
    const b = branches[i];
    if (!convId || busy || !b || k < 0 || k >= b.versions.length || k === b.idx) return;
    const versions = [...b.versions];
    versions[b.idx] = messages.slice(i);
    const next = [...messages.slice(0, i), ...versions[k]];
    setBranches((all) => ({ ...Object.fromEntries(Object.entries(all).filter(([key]) => Number(key) < i)), [i]: { versions, idx: k } }));
    setMessages(next);
    await store.replaceMessages(convId, next.map(persist));
  }, [branches, busy, convId, messages, store]);

  const rename = useCallback(async (id: ID, title: string) => {
    await store.renameConversation(id, title);
    refresh();
  }, [refresh, store]);

  const pin = useCallback(async (id: ID, pinned: boolean) => {
    await store.setPinned?.(id, pinned);
    refresh();
  }, [refresh, store]);

  const archive = useCallback(async (id: ID, archived: boolean) => {
    await store.setArchived?.(id, archived);
    refresh();
  }, [refresh, store]);

  /** `{current, total}` for the version arrows under user message `i`, if it was ever edited. */
  const versionOf = (i: number) => {
    const b = branches[i];
    // `answersOnly`: every version is the same question re-answered (regenerate), so the arrows sit under
    // the answer like ChatGPT/Claude; edits keep them under the question.
    return b ? { current: b.idx + 1, total: b.versions.length, answersOnly: b.versions.every((v) => v[0]?.id === b.versions[0][0]?.id) } : undefined;
  };

  // Dev/perf hook only. Activated by ?audit=1; it never talks to the network.
  useEffect(() => {
    if (typeof window === 'undefined' || !new URLSearchParams(window.location.search).has('audit')) return;
    const paragraph = (i: number) =>
      `Denial rate for group ${i} is ${((i * 3) % 19) + 4}%. **Net** of reversals, the month moved about ${(i % 5) + 1} points.\n\n- Check the payer mix\n- September is still open`;
    const assistant = (id: string, parts: AssistantViewV1['parts']): ViewMessage => ({
      id, role: 'assistant', createdAt: '2026-10-01T00:00:00Z', streaming: false, status: null, openText: [], parts, steps: [], escalations: [], lastSeq: -1,
    });
    window.__ccAudit = {
      seedMessages(count: number) {
        const msgs: ViewMessage[] = [];
        for (let i = 0; i < count; i++) {
          if (i % 2 === 0) msgs.push({ id: `u${i}`, role: 'user', text: `What changed in denial rate for payer group ${i}?`, createdAt: '2026-10-01T00:00:00Z' });
          else msgs.push(assistant(`a${i}`, [{ type: 'text', id: `t${i}`, text: paragraph(i) }]));
        }
        setConvId('audit');
        setBranches({});
        setBusy(false);
        setMessages(msgs);
      },
      seedTable(rowCount: number) {
        const columns: TablePart['columns'] = [
          { key: 'payer', label: 'Payer', type: 'string' },
          { key: 'claims', label: 'Claims', type: 'integer' },
          { key: 'rate', label: 'Denial rate', type: 'percent' },
          { key: 'amt', label: 'Denied $', type: 'currency' },
        ];
        const rows = Array.from({ length: rowCount }, (_, i) => [`Payer ${i % 80}`, 1000 + i, (i % 50) / 100, Math.round(i * 12.4)]);
        const part: TablePart = { type: 'table', id: 'audit-table', title: 'Preview', columns, rows, rowCount, source: { views: ['sem.claims'], elapsedMs: 40 } };
        setConvId('audit');
        setBranches({});
        setBusy(false);
        setMessages([
          { id: 'u-table', role: 'user', text: 'Show the full preview.', createdAt: '2026-10-01T00:00:00Z' },
          assistant('a-table', [part]),
        ]);
      },
      async burst(tokens: number, gapMs = 8) {
        const existing = messagesRef.current;
        const history: ViewMessage[] = existing.length
          ? [...existing, { id: 'u-burst', role: 'user', text: 'Explain the denial trend in detail.', createdAt: '2026-10-01T00:00:00Z' }]
          : [{ id: 'u-burst', role: 'user', text: 'Explain the denial trend in detail.', createdAt: '2026-10-01T00:00:00Z' }];
        let view = newAssistantViewV1('a-burst');
        let seq = 0;
        view = reduceV1(view, { type: 'message.start', seq: seq++, messageId: 'a-burst', conversationId: 'audit', contractVersion: 'v1' });
        view = reduceV1(view, { type: 'text.start', seq: seq++, partId: 'p-burst' });
        setConvId('audit');
        setBusy(true);
        const bits = ['Denial ', 'rates ', 'rose ', 'through ', 'Q3 ', 'for ', 'the ', 'largest ', 'payers. '];
        const batch = frameBatch(() => setMessages([...history, view]));
        batch.now();
        for (let i = 0; i < tokens; i++) {
          view = reduceV1(view, { type: 'text.delta', seq: seq++, partId: 'p-burst', delta: bits[i % bits.length] });
          batch.schedule();
          if (gapMs > 0) await new Promise((resolve) => setTimeout(resolve, gapMs));
        }
        view = reduceV1(view, { type: 'text.end', seq: seq++, partId: 'p-burst' });
        view = { ...view, streaming: false, status: null, openText: [] };
        batch.now();
        setBusy(false);
      },
    };
    return () => { delete window.__ccAudit; };
  }, []);

  return { threads, convId, messages, busy, send, stop, open, newChat, regenerate, remove, editAt, switchVersion, versionOf, rename, pin, canPin: !!store.setPinned, archive, canArchive: !!store.setArchived };
}
