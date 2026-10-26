import { Component, type ReactNode } from 'react';
import { reportError, type ErrorRecord } from '../lib/errorReporting';
import { buildDiagnostics, isChunkLoadError } from '../lib/diagnostics';
import { copyText } from '../lib/csv';

interface Props {
  children: ReactNode;
  /** 'app' (default) replaces the page with the crash screen. 'section' shows an inline card with "Try again". */
  scope?: 'app' | 'section';
  /** Allowlisted component name for the report's context, e.g. 'App' or 'ArtifactPanel'. Never user text. */
  name?: string;
}
interface State {
  rec: ErrorRecord | null;
  failed: boolean;
  chunk: boolean;
  copied: 'idle' | 'ok' | 'fail';
}

/**
 * The render-crash fallback. It never shows the error message (it can hold PHI and ends up in screenshots). It shows
 * a reference id that matches the redacted record from reportError(), plus copyable diagnostics built only from
 * allowlisted facts (lib/diagnostics.ts).
 */
export class ErrorBoundary extends Component<Props, State> {
  state: State = { rec: null, failed: false, chunk: false, copied: 'idle' };

  static getDerivedStateFromError(e: unknown): Partial<State> {
    return { failed: true, chunk: isChunkLoadError(e) };
  }

  componentDidCatch(error: unknown) {
    const component = this.props.name ?? (this.props.scope === 'section' ? 'Section' : 'App');
    this.setState({ rec: reportError(error, 'render', { component, code: isChunkLoadError(error) ? 'chunk_load' : undefined }) });
  }

  private reset = () => this.setState({ rec: null, failed: false, chunk: false, copied: 'idle' });

  private copy = async () => {
    if (!this.state.rec) return;
    this.setState({ copied: (await copyText(buildDiagnostics(this.state.rec))) ? 'ok' : 'fail' });
  };

  /** Drops the conversation route so a conversation that crashes on load can't trap the user in a reload loop. */
  private newChat = () => {
    window.location.assign(window.location.pathname + window.location.search);
  };

  render() {
    const { rec, failed, chunk, copied } = this.state;
    if (!failed) return this.props.children;
    const diag = rec ? buildDiagnostics(rec) : '';

    if (this.props.scope === 'section') {
      return (
        <div role="alert" data-testid="section-crash" className="my-2 rounded-xl border border-line bg-muted p-3 text-sm text-fg-muted">
          <p>{chunk ? "This part couldn't load. Check your connection." : "This part couldn't be displayed."}{rec && <span className="ml-1 text-xs text-fg-subtle">Ref {rec.id}</span>}</p>
          <button type="button" onClick={this.reset} className="mt-2 rounded-full border border-line px-3 py-1 text-xs font-medium text-fg hover:bg-hover">
            Try again
          </button>
        </div>
      );
    }

    return (
      <main role="alert" data-testid="app-crash" className="flex min-h-screen flex-col items-center justify-center gap-3 bg-bg p-6 text-center font-sans text-fg">
        <h1 className="text-lg font-semibold">{chunk ? "Couldn't load part of the app" : 'Something went wrong'}</h1>
        <p className="max-w-sm text-sm text-fg-muted">
          {chunk
            ? 'The connection may have dropped, or a new version was just released. Reload to continue. Your chats are saved on the server.'
            : 'The page hit an unexpected error. Your chats are saved on the server. Reload to continue.'}
        </p>
        <div className="flex flex-wrap items-center justify-center gap-2">
          <button type="button" autoFocus onClick={() => window.location.reload()} className="rounded-full bg-fg px-4 py-2 text-sm font-medium text-bg hover:opacity-90">
            Reload
          </button>
          <button type="button" onClick={this.newChat} className="rounded-full border border-line px-4 py-2 text-sm font-medium hover:bg-hover">
            Start a new chat
          </button>
        </div>
        {rec && (
          <details className="mt-2 w-full max-w-md text-left text-xs text-fg-subtle">
            <summary className="cursor-pointer text-center">Reference {rec.id} · diagnostics for support</summary>
            <p className="mt-2 text-fg-muted">Includes no message text or patient data. Below is exactly what gets copied.</p>
            <pre data-testid="crash-diagnostics" className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-muted p-2 font-mono text-[11px] leading-snug">{diag}</pre>
            <div className="mt-2 flex items-center gap-2">
              <button type="button" onClick={this.copy} className="rounded-full border border-line px-3 py-1 font-medium text-fg hover:bg-hover">
                Copy diagnostics
              </button>
              <span aria-live="polite">{copied === 'ok' ? 'Copied' : copied === 'fail' ? "Couldn't copy. Select the text above." : ''}</span>
            </div>
          </details>
        )}
      </main>
    );
  }
}
