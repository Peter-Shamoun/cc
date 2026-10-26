import { Component, type ReactNode } from 'react';
import { reportError } from '../lib/errorReporting';

interface State {
  ref: string | null;
}

/**
 * The top-level render-crash fallback. It never shows the error message (it can hold PHI and ends up in
 * screenshots). It shows only a reference id that matches the redacted record from reportError().
 */
export class ErrorBoundary extends Component<{ children: ReactNode }, State> {
  state: State = { ref: null };

  static getDerivedStateFromError(): Partial<State> {
    return { ref: 'pending' };
  }

  componentDidCatch(error: unknown) {
    this.setState({ ref: reportError(error, 'render', { component: 'App' }).id });
  }

  render() {
    if (this.state.ref === null) return this.props.children;
    return (
      <main role="alert" data-testid="app-crash" className="flex min-h-screen flex-col items-center justify-center gap-3 bg-bg p-6 text-center font-sans text-fg">
        <h1 className="text-lg font-semibold">Something went wrong</h1>
        <p className="max-w-sm text-sm text-fg-muted">The page hit an unexpected error. Your chats are saved on the server. Reload to continue.</p>
        <button type="button" onClick={() => window.location.reload()} className="rounded-full bg-fg px-4 py-2 text-sm font-medium text-bg hover:opacity-90">
          Reload
        </button>
        {this.state.ref !== 'pending' && <p className="text-xs text-fg-subtle">Reference {this.state.ref}</p>}
      </main>
    );
  }
}
