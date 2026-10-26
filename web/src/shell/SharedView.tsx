import { useEffect, useMemo, useState } from 'react';
import { Code2, EyeOff, Lock, SquarePen } from 'lucide-react';
import { AssistantTurn, UserBubble, type Artifact } from '../components/Message';
import { ArtifactPanel } from '../components/ArtifactPanel';
import type { AnyView } from '../components/answer/normalize';
import { HttpAnalystAdapter } from '../adapters/http';
import { hydrate } from '../adapters/httpStore';
import { markAnswered, toView, type ViewMessage } from '../lib/useChat';
import { foldThread } from '../lib/threadTree';
import type { Message } from '../lib/contract';
import type { SharedConversation } from '../contract/v1.gen';
import { makeAdapter, ThreadSkeleton } from './AppShell';
import { ToastProvider } from './primitives';
import { withholdSql } from './withholdSql';

type State = { kind: 'loading' } | { kind: 'gone' } | { kind: 'signin' } | { kind: 'error' } | { kind: 'ok'; shared: SharedConversation; messages: ViewMessage[] };

const noop = () => {};

/**
 * `/shared/<id>` (contract 1.11.0 §17): someone's chat, shared inside the organization. Read-only, like a
 * ChatGPT/Claude shared page: no composer, regenerate, edit or feedback. Unknown, revoked and other-org links
 * all read the same "isn't available", so a link reveals nothing. The share id is never logged or shown.
 */
export function SharedView({ shareId }: { shareId: string }) {
  const adapter = useMemo(() => makeAdapter(), []);
  const [state, setState] = useState<State>(() => (adapter instanceof HttpAnalystAdapter ? { kind: 'loading' } : { kind: 'gone' }));
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  useEffect(() => {
    if (!(adapter instanceof HttpAnalystAdapter)) return;
    let live = true;
    adapter.loadShared(shareId).then(
      (shared) => {
        if (!live) return;
        if (!shared) { setState({ kind: 'gone' }); return; }
        shared = withholdSql(shared);
        const { visible } = foldThread((shared.messages as unknown as Message[]).map(hydrate).map((m) => toView(m)));
        setState({ kind: 'ok', shared, messages: markAnswered(visible) });
      },
      (e: unknown) => { if (live) setState({ kind: /\b401\b/.test(String(e)) ? 'signin' : /\b410\b/.test(String(e)) ? 'gone' : 'error' }); },
    );
    return () => { live = false; };
  }, [adapter, shareId]);
  // A fixed title, never the chat's own: titles echo the question (a member's name, a claim id), and browsers keep
  // page titles in history, sync them across devices and show them in tab strips during screen shares.
  useEffect(() => { document.title = 'Shared chat · CC'; }, []);

  return (
    <ToastProvider>
      <div className="flex h-dvh flex-col bg-bg font-sans text-fg" data-testid="shared-view">
        <header className="flex h-[52px] shrink-0 items-center justify-between border-b border-line/60 px-4">
          <a href="/" className="flex items-center gap-2 rounded-md font-semibold">
            <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-[11px] font-bold text-accent-fg" aria-hidden>CC</span>
            CC
          </a>
          <a href="/" className="flex items-center gap-1.5 rounded-full bg-fg px-3 py-1.5 text-sm text-bg hover:opacity-90" data-testid="shared-new-chat">
            <SquarePen size={14} aria-hidden /> Start a new chat
          </a>
        </header>
        <div className="flex min-h-0 flex-1">
          <main className="min-w-0 flex-1 overflow-y-auto" aria-labelledby="shared-title">
            <div className="mx-auto flex max-w-[48rem] flex-col gap-6 px-4 pb-16 pt-8 md:px-6">
              {state.kind === 'loading' && <ThreadSkeleton />}
              {state.kind !== 'loading' && state.kind !== 'ok' && <Unavailable kind={state.kind} />}
              {state.kind === 'ok' && (
                <>
                  <div className="border-b border-line pb-5">
                    <h1 id="shared-title" className="text-2xl font-semibold tracking-tight">{state.shared.title || 'Shared chat'}</h1>
                    <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-fg-subtle">
                      <span className="flex items-center gap-1.5"><Lock size={13} aria-hidden /> Shared within your organization · Read-only</span>
                      {state.shared.updatedAt && <span>{new Date(state.shared.updatedAt).toLocaleDateString(undefined, { month: 'long', day: 'numeric', year: 'numeric' })}</span>}
                      {!state.shared.includeData && (
                        <span className="flex items-center gap-1 text-xs text-fg-muted" data-testid="shared-no-data">
                          <EyeOff size={12} aria-hidden /> Result data not shared
                        </span>
                      )}
                      {(state.shared.sqlWithheld || !state.shared.includeData) && (
                        <span className="flex items-center gap-1 text-xs text-fg-muted" data-testid="shared-no-sql">
                          <Code2 size={12} aria-hidden /> SQL not shared
                        </span>
                      )}
                    </p>
                  </div>
                  <div className="flex flex-col gap-6" role="feed" aria-label="Messages" aria-busy="false">
                    {state.messages.map((m) => (
                      <article key={m.id} data-message-id={m.id} aria-label={m.role === 'user' ? 'Question' : 'Answer'}>
                        {m.role === 'user'
                          ? <UserBubble msg={m} />
                          : <AssistantTurn msg={m as AnyView} isLast={false} readOnly onOpen={setArtifact} onRegenerate={noop} onEdit={noop} sqlWithheld={!state.shared.includeData || !!state.shared.sqlWithheld} />}
                      </article>
                    ))}
                  </div>
                  <p className="border-t border-line pt-4 text-xs text-fg-subtle">
                    Answers can be wrong; check important numbers against the source data. De-identified demo data.
                  </p>
                </>
              )}
            </div>
          </main>
          {artifact && <ArtifactPanel artifact={artifact} onClose={() => setArtifact(null)} />}
        </div>
      </div>
    </ToastProvider>
  );
}

function Unavailable({ kind }: { kind: 'gone' | 'signin' | 'error' }) {
  const copy = {
    gone: { h: "This link isn't available", p: 'It may have been turned off, or it belongs to a different organization.' },
    signin: { h: 'Sign in to view this chat', p: 'Shared chats are only visible to people signed in to the same organization.' },
    error: { h: "Couldn't load this chat", p: 'Check your connection and reload the page.' },
  }[kind];
  return (
    <div className="mx-auto mt-16 max-w-sm text-center" data-testid="shared-unavailable" role="alert">
      <div className="mx-auto mb-4 flex h-10 w-10 items-center justify-center rounded-full bg-muted text-fg-muted"><Lock size={18} aria-hidden /></div>
      <h1 id="shared-title" className="text-lg font-semibold">{copy.h}</h1>
      <p className="mt-2 text-sm text-fg-muted">{copy.p}</p>
      <a href="/" className="mt-6 inline-flex rounded-full border border-line px-4 py-2 text-sm hover:bg-hover">Go to CC</a>
    </div>
  );
}
