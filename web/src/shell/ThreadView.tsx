import { memo, useRef, type CSSProperties, type MutableRefObject, type RefObject } from 'react';
import { useVirtualizer } from '@tanstack/react-virtual';
import { AssistantTurn, REFINE_PROMPT, UserBubble, messageMarkdown, type Artifact } from '../components/Message';
import type { ViewMessage } from '../lib/useChat';
import type { AnyView } from '../components/answer/normalize';
import type { OnReply } from '../components/answer/States';

/** Callbacks for the thread rows. Held in a ref so rows can be memoised by message identity (QA-S7). */
export interface ThreadActions {
  editAt: (i: number, text: string) => void;
  switchVersion: (i: number, k: number) => void;
  versionOf: (i: number) => { current: number; total: number } | undefined;
  openArtifact: (a: Artifact) => void;
  regenerate: () => void;
  editIntoComposer: (t: string) => void;
  send: (t: string) => void;
  /** Clarify/abstain replies (answer team: set this to resume-aware send once useChat takes {resume}). */
  reply?: OnReply;
}

/**
 * Old turns skip layout/paint until scrolled near (content-visibility:auto); the browser keeps
 * their last measured height (`auto` intrinsic size) so the scrollbar doesn't jump. The newest
 * few turns always render fully so streaming, pinning and measuring stay exact.
 */
const LIVE_TAIL = 6;
/** Past this length the thread window is virtualized. Shorter chats stay a plain list so tests and small threads are unchanged. */
const VIRTUAL_AT = 30;
const offscreen: CSSProperties = { contentVisibility: 'auto', containIntrinsicSize: 'auto 320px' } as CSSProperties;

interface RowProps {
  msg: ViewMessage;
  index: number;
  isLast: boolean;
  /** Newest answer reserves most of a viewport so the pinned question can sit at the top (QA-S3). */
  reserve: boolean;
  lazy: boolean;
  busy: boolean;
  vCur?: number;
  vTotal?: number;
  actions: React.MutableRefObject<ThreadActions>;
}

const Row = memo(function Row({ msg, index, isLast, reserve, lazy, busy, vCur, vTotal, actions }: RowProps) {
  const a = actions.current;
  if (msg.role === 'user') {
    return (
      <div data-message-id={msg.id} style={lazy ? offscreen : undefined}>
        <UserBubble msg={msg} disabled={busy} onEdit={(t) => actions.current.editAt(index, t)}
          version={vCur !== undefined && vTotal !== undefined ? { current: vCur, total: vTotal } : undefined}
          onVersion={(k) => actions.current.switchVersion(index, k)} />
      </div>
    );
  }
  return (
    <div data-message-id={msg.id} style={reserve ? { minHeight: 'calc(100dvh - 330px)' } : lazy ? offscreen : undefined}>
      <AssistantTurn msg={msg as AnyView} isLast={isLast} onOpen={(x) => actions.current.openArtifact(x)} onRegenerate={() => actions.current.regenerate()}
        onEdit={(t) => actions.current.editIntoComposer(t)} onSend={(t) => actions.current.send(t)}
        onReply={a.reply ? (t, r) => actions.current.reply?.(t, r) : undefined}
        onRefine={isLast ? (style) => actions.current.send(REFINE_PROMPT[style]) : undefined} />
    </div>
  );
});

/** Plain, searchable text for a turn (markdown syntax stripped). Cached per message object. */
const plainCache = new WeakMap<object, string>();
export function plainText(m: ViewMessage): string {
  const hit = plainCache.get(m);
  if (hit !== undefined) return hit;
  let raw = '';
  try { raw = m.role === 'user' ? m.text : messageMarkdown(m as AnyView); } catch { raw = ''; }
  const out = raw
    .replace(/```[^\n]*\n?/g, '')
    .replace(/!\[[^\]]*\]\([^)]*\)/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/^\s{0,3}(#{1,6}|>|[-*+]|\d+\.)\s+/gm, '')
    .replace(/^\s*\|?\s*:?-{2,}.*$/gm, '')
    .replace(/\|/g, ' ')
    .replace(/[*_`~]+/g, '')
    .replace(/[ \t]+/g, ' ')
    .replace(/^ +| +$/gm, '')
    .trim();
  plainCache.set(m, out);
  return out;
}

/**
 * QA-S13: a virtualized turn outside the window isn't in the DOM, so the browser's find (Ctrl+F)
 * can't reach it. Each such turn keeps a lightweight text "shadow" at its virtual position: plain
 * text, no React subtree, skipped by layout until near (content-visibility). Find matches it and
 * scrolls there, the window moves, and the full turn replaces it. Hidden from assistive tech (the
 * full row is the accessible one), inert to pointer events.
 */
const Shadow = memo(function Shadow({ msg, start, size }: { msg: ViewMessage; start: number; size: number }) {
  return (
    <div aria-hidden="true" data-find-shadow={msg.id}
      className={`pointer-events-none absolute left-0 top-0 w-full overflow-hidden whitespace-pre-wrap break-words pb-6 text-[15px] leading-7 text-[var(--fg)] ${msg.role === 'user' ? 'pl-[20%] text-right' : ''}`}
      style={{ transform: `translateY(${start}px)`, height: size, contentVisibility: 'auto', containIntrinsicSize: `auto ${size}px` } as CSSProperties}>
      {plainText(msg)}
    </div>
  );
});

function auditVisit(count: number) {
  if (typeof window === 'undefined' || !new URLSearchParams(window.location.search).has('audit')) return;
  const stats = (window.__ccRenderStats ??= { commits: 0, actual: 0, worst: 0, children: 0 });
  stats.children += count;
}

/** The message list. Only rows whose message object or position flags changed re-render while a reply streams. */
export function ThreadView({ messages, busy, actions, scrollRef, scrollToIndexRef, renderAll }: {
  messages: ViewMessage[];
  busy: boolean;
  actions: ThreadActions;
  scrollRef?: RefObject<HTMLDivElement | null>;
  /** Virtual lists pin by index. Null while the list is short enough to scroll the real DOM node. */
  scrollToIndexRef?: MutableRefObject<((index: number) => void) | null>;
  /** Render every turn (printing): the virtual window would print only what is on screen. */
  renderAll?: boolean;
}) {
  const ref = useRef(actions);
  ref.current = actions;
  const n = messages.length;
  const virtual = n > VIRTUAL_AT && !!scrollRef && !renderAll;
  const virtualizer = useVirtualizer({
    count: virtual ? n : 0,
    getScrollElement: () => scrollRef?.current ?? null,
    estimateSize: () => 160,
    overscan: 6,
    getItemKey: (index) => messages[index]?.id ?? index,
    // Paint a window before the scrollport is measured, so the first frame is not blank.
    initialRect: { width: 720, height: 800 },
  });
  if (scrollToIndexRef) {
    scrollToIndexRef.current = virtual
      ? (index) => { virtualizer.scrollToIndex(index, { align: 'start', behavior: 'auto' }); }
      : null;
  }
  const items = virtual ? virtualizer.getVirtualItems() : [];
  auditVisit(virtual ? items.length : n);

  const row = (m: ViewMessage, i: number) => {
    const v = m.role === 'user' ? actions.versionOf(i) : undefined;
    const isLast = i === n - 1;
    return (
      <Row key={('uiKey' in m && m.uiKey) || m.id} msg={m} index={i} isLast={isLast} reserve={isLast && m.role === 'assistant'} lazy={!virtual && i < n - LIVE_TAIL}
        busy={m.role === 'user' ? busy : false} vCur={v?.current} vTotal={v?.total} actions={ref} />
    );
  };

  if (!virtual) {
    return (
      <>
        {messages.map((m, i) => row(m, i))}
      </>
    );
  }

  const shown = new Set(items.map((it) => it.index));
  const all = virtualizer.measurementsCache;
  return (
    <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
      {all.map((it) => {
        const m = messages[it.index];
        return m && !shown.has(it.index) ? <Shadow key={`s-${String(it.key)}`} msg={m} start={it.start} size={it.size} /> : null;
      })}
      {items.map((item) => {
        const m = messages[item.index];
        if (!m) return null;
        return (
          <div key={String(item.key)} data-index={item.index} ref={virtualizer.measureElement}
            className="absolute left-0 top-0 w-full pb-6" style={{ transform: `translateY(${item.start}px)` }}>
            {row(m, item.index)}
          </div>
        );
      })}
    </div>
  );
}
