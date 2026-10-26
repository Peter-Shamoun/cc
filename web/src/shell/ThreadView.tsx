import { memo, useRef, type CSSProperties } from 'react';
import { AssistantTurn, UserBubble, type Artifact } from '../components/Message';
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
        onReply={a.reply ? (t, r) => actions.current.reply?.(t, r) : undefined} />
    </div>
  );
});

/** The message list. Only rows whose message object or position flags changed re-render while a reply streams. */
export function ThreadView({ messages, busy, actions }: { messages: ViewMessage[]; busy: boolean; actions: ThreadActions }) {
  const ref = useRef(actions);
  ref.current = actions;
  const n = messages.length;
  return (
    <>
      {messages.map((m, i) => {
        const v = m.role === 'user' ? actions.versionOf(i) : undefined;
        const isLast = i === n - 1;
        return (
          <Row key={m.id} msg={m} index={i} isLast={isLast} reserve={isLast && m.role === 'assistant'} lazy={i < n - LIVE_TAIL}
            busy={m.role === 'user' ? busy : false} vCur={v?.current} vTotal={v?.total} actions={ref} />
        );
      })}
    </>
  );
}
