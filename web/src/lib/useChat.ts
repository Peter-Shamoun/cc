import { useCallback, useEffect, useRef, useState } from 'react';
import { newAssistantView, reduce, type AssistantMessage, type AssistantView, type ID, type UserMessage } from './contract';
import type { AnalystAdapter, TranscriptStore } from '../adapters/types';

const uid = (p: string) => `${p}_${Math.random().toString(36).slice(2, 10)}`;
export type ViewMessage = UserMessage | AssistantView;

function toView(m: UserMessage | AssistantMessage): ViewMessage {
  return m.role === 'user' ? m : { ...m, streaming: false, status: null, openText: [] };
}

/** Placeholder title until the server returns a PHI-scrubbed one (spec §6.5). */
function draftTitle(text: string) {
  return text.length > 44 ? `${text.slice(0, 44)}…` : text;
}

export function useChat(adapter: AnalystAdapter, store: TranscriptStore) {
  const [threads, setThreads] = useState<{ id: ID; title: string; updatedAt: string }[]>([]);
  const [convId, setConvId] = useState<ID | null>(null);
  const [messages, setMessages] = useState<ViewMessage[]>([]);
  const [busy, setBusy] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => setThreads((await store.listConversations()).items), [store]);
  useEffect(() => {
    refresh();
  }, [refresh]);

  const open = useCallback(async (id: ID) => {
    abortRef.current?.abort();
    const c = await store.getConversation(id);
    setConvId(c ? id : null);
    setMessages(c ? c.messages.map(toView) : []);
  }, [store]);

  const newChat = useCallback(() => {
    abortRef.current?.abort();
    setConvId(null);
    setMessages([]);
  }, []);

  const run = useCallback(async (cid: ID, history: ViewMessage[]) => {
    const ctrl = new AbortController();
    abortRef.current = ctrl;
    let view = newAssistantView(uid('a'));
    const set = () => setMessages([...history, view]);
    set();
    setBusy(true);
    const wire = history.map((m) => ({ id: m.id, role: m.role, text: m.role === 'user' ? m.text : m.parts.map((p) => (p.type === 'text' ? p.text : '')).join('\n') }));
    try {
      for await (const ev of adapter.streamTurn({ conversationId: cid, messages: wire, signal: ctrl.signal })) {
        view = reduce(view, ev);
        set();
        if (ev.type === 'message.end') break;
      }
    } catch (e) {
      if ((e as Error).name !== 'AbortError') {
        view = reduce(view, { type: 'part', part: { type: 'error', id: uid('err'), code: 'internal', title: 'Connection lost', message: 'Response interrupted.', retryable: true } });
      }
    }
    if (ctrl.signal.aborted && view.streaming) view = reduce(view, { type: 'message.end', messageId: view.id, finishReason: 'aborted' });
    view = { ...view, streaming: false, status: null, openText: [] };
    set();
    setBusy(false);
    const { streaming: _s, status: _st, openText: _o, finishReason: _f, usage: _u, ...persist } = view;
    void _s; void _st; void _o; void _f; void _u;
    await store.appendMessage(cid, persist);
    refresh();
  }, [adapter, refresh, store]);

  const send = useCallback(async (text: string) => {
    const t = text.trim();
    if (!t || busy) return;
    const cid = convId ?? uid('c');
    if (!convId) setConvId(cid);
    const user: UserMessage = { id: uid('u'), role: 'user', text: t, createdAt: new Date().toISOString() };
    await store.appendMessage(cid, user, draftTitle(t));
    await run(cid, [...messages, user]);
  }, [busy, convId, messages, run, store]);

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

  return { threads, convId, messages, busy, send, stop, open, newChat, regenerate, remove };
}
