import { useCallback, useEffect, useRef, useState } from 'react';
import type { AssistantMessage, ID, UserMessage } from './contract';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type ChatRequest, type Usage } from '../api/contract';
import type { AnalystAdapter, ThreadSummary, TranscriptStore } from '../adapters/types';
import { shortTitle } from '../shell/title';

const uid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 10)}`;
export type ViewMessage = UserMessage | AssistantViewV1;

function toView(m: UserMessage | AssistantMessage): ViewMessage {
  if (m.role === 'user') return m;
  const v = m as AssistantMessage & Partial<AssistantViewV1>;
  return { ...v, parts: v.parts as AssistantViewV1['parts'], streaming: false, status: null, openText: [], steps: v.steps ?? [], escalations: v.escalations ?? [], lastSeq: -1 };
}

/**
 * A clarification that has a later turn after it was replied to (CONTRACT.md §6), so after a reload
 * it renders as answered instead of offering choices again. The server sets `answered` on its own
 * transcript; the memory store does not, so we derive it here too.
 */
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

export function useChat(adapter: AnalystAdapter, store: TranscriptStore) {
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

  const refresh = useCallback(async () => setThreads((await store.listConversations()).items), [store]);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const open = useCallback(async (id: ID) => {
    abortRef.current?.abort();
    const c = await store.getConversation(id);
    setConvId(c ? id : null);
    setBranches({});
    setMessages(c ? markAnswered(c.messages.map(toView)) : []);
  }, [store]);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    setConvId(null);
    setBranches({});
    setMessages([]);
  }, []);

  const run = useCallback(async (cid: ID, history: ViewMessage[], resume?: ChatRequest['resume']) => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let view = newAssistantViewV1(uid('a'));
    const startedAt = Date.now();
    history = markAnswered(history);
    const set = () => setMessages([...history, view]);
    set();
    setBusy(true);
    const wire = history.map((m) => ({ id: m.id, role: m.role, text: m.role === 'user' ? m.text : m.parts.map((p) => (p.type === 'text' ? p.text : '')).join('\n') }));
    try {
      for await (const ev of adapter.streamTurn({ conversationId: cid, messages: wire, signal: ctrl.signal, ...(resume ? { resume } : {}) })) {
        view = reduceV1(view, ev);
        set();
        if (ev.type === 'message.end') break;
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError' && !ctrl.signal.aborted) {
        view = reduceV1(view, { type: 'part', part: { type: 'error', id: uid('err'), code: 'internal', title: 'Connection lost', message: 'Response interrupted.', retryable: true } });
        if (view.streaming) view = reduceV1(view, { type: 'message.end', messageId: view.id, finishReason: 'error', usage: partialUsage(view, startedAt) } as never);
      }
    }
    // Stopped before the server's aborted message.end arrived: keep what ran (queries, time) on the answer.
    if (ctrl.signal.aborted && view.streaming) view = reduceV1(view, { type: 'message.end', messageId: view.id, finishReason: 'aborted', usage: partialUsage(view, startedAt) } as never);
    view = { ...view, streaming: false, status: null, openText: [] };
    set();
    setBusy(false);
    await store.appendMessage(cid, persist(view));
    refresh();
  }, [adapter, refresh, store]);

  /** Send a user turn. `resume` answers a clarification (CONTRACT.md §6) so the server continues that run. */
  const send = useCallback(async (text: string, opts?: { resume?: ChatRequest['resume'] }) => {
    const t = text.trim();
    if (!t || busy) return;
    const cid = convId ?? uid('c');
    if (!convId) setConvId(cid);
    const user: UserMessage = { id: uid('u'), role: 'user', text: t, createdAt: new Date().toISOString() };
    await store.appendMessage(cid, user, draftTitle(t));
    refresh(); // show the new chat in the sidebar now, not when the first answer ends (QA-S2)
    await run(cid, [...messages, user], opts?.resume);
  }, [busy, convId, messages, refresh, run, store]);

  const stop = useCallback(() => abortRef.current?.abort(), []);

  const regenerate = useCallback(async () => {
    if (!convId || busy) return;
    const lastUserIdx = messages.map((m) => m.role).lastIndexOf('user');
    if (lastUserIdx < 0) return;
    await store.truncateAfter(convId, messages[lastUserIdx].id);
    await run(convId, messages.slice(0, lastUserIdx + 1));
  }, [busy, convId, messages, run, store]);

  const remove = useCallback(async (id: ID) => {
    await store.deleteConversation(id);
    if (convId === id) newChat();
    refresh();
  }, [convId, newChat, refresh, store]);

  /** Edit user message `i`: keep the old tail as a version, run the edited question as a new one. */
  const editAt = useCallback(async (i: number, text: string) => {
    const t = text.trim();
    const old = messages[i];
    if (!convId || busy || !t || old?.role !== 'user' || t === old.text) return;
    const tail = messages.slice(i);
    const b = branches[i] ?? { versions: [tail], idx: 0 };
    const versions = [...b.versions];
    versions[b.idx] = tail;
    const user: UserMessage = { id: uid('u'), role: 'user', text: t, createdAt: new Date().toISOString() };
    versions.push([user]);
    setBranches((all) => ({ ...Object.fromEntries(Object.entries(all).filter(([k]) => Number(k) < i)), [i]: { versions, idx: versions.length - 1 } }));
    const prefix = messages.slice(0, i);
    await store.replaceMessages(convId, [...prefix, user].map(persist));
    await run(convId, [...prefix, user]);
  }, [branches, busy, convId, messages, run, store]);

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

  /** `{current, total}` for the version arrows under user message `i`, if it was ever edited. */
  const versionOf = (i: number) => {
    const b = branches[i];
    return b ? { current: b.idx + 1, total: b.versions.length } : undefined;
  };

  return { threads, convId, messages, busy, send, stop, open, newChat, regenerate, remove, editAt, switchVersion, versionOf, rename, pin, canPin: !!store.setPinned };
}
