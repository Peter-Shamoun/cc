import type { ReactNode } from 'react';
import { useEffect } from 'react';

/** Quiet full-page status (404, signed out, session expired): small mark, one plain heading, one action. */
function StatusPage({ title, children, action, testId }: { title: string; children: ReactNode; action: { label: string; href: string }; testId: string }) {
  return (
    <div className="flex min-h-dvh flex-col bg-bg font-sans text-fg" data-testid={testId}>
      <header className="flex h-[52px] items-center gap-2 px-4 text-[15px] font-semibold">
        <span className="flex h-6 w-6 items-center justify-center rounded-md bg-accent text-[11px] font-bold text-accent-fg" aria-hidden>CC</span>
        ClaimClarity
      </header>
      <main className="flex flex-1 items-center justify-center px-6 pb-[12vh]">
        <div className="w-full max-w-sm">
          <h1 className="text-xl font-semibold tracking-tight">{title}</h1>
          <p className="mt-2 text-sm leading-6 text-fg-muted">{children}</p>
          <a href={action.href} className="mt-6 inline-flex h-9 items-center rounded-lg bg-accent px-4 text-sm font-medium text-accent-fg hover:opacity-90 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[var(--focus)]">{action.label}</a>
        </div>
      </main>
    </div>
  );
}

const home = () => import.meta.env.BASE_URL || '/';

export function NotFoundPage() {
  // document.title is a PHI sink (security/titleSink.test.tsx): literal strings only.
  useEffect(() => { document.title = 'Page not found · ClaimClarity'; }, []);
  return (
    <StatusPage title="Page not found" testId="not-found" action={{ label: 'Go to ClaimClarity', href: home() }}>
      This link doesn’t go anywhere. It may be mistyped, or the page may have moved.
    </StatusPage>
  );
}

export function SignedOutPage({ expired, signInUrl }: { expired: boolean; signInUrl?: string }) {
  useEffect(() => {
    if (expired) document.title = 'Your session expired · ClaimClarity';
    else document.title = 'You’re signed out · ClaimClarity';
  }, [expired]);
  return (
    <StatusPage title={expired ? 'Your session expired' : 'You’re signed out'} testId="signed-out" action={{ label: 'Sign in again', href: signInUrl || home() }}>
      {expired ? 'For your security, you were signed out. Sign in again to continue where you left off.' : 'This browser no longer has access to your chats. Sign in again to get back to them.'}
    </StatusPage>
  );
}
