import { Component, type ReactNode } from 'react';
import { AlertTriangle } from 'lucide-react';
import { reportError, type ErrorRecord } from '../lib/errorReporting';
import { buildDiagnostics, isChunkLoadError } from '../lib/diagnostics';
import { copyText } from '../lib/csv';

interface Props {
  children: ReactNode;
  /** 'app' (default) replaces the page with the crash screen. 'section' shows a quiet inline notice with "Try again". */
  scope?: 'app' | 'section';
  /** Allowlisted component name for the report's context, e.g. 'App' or 'ArtifactPanel'. Never user text. */
  name?: string;
  /** Section scope: a second action next to "Try again", e.g. closing the artifact panel that crashed. */
  onDismiss?: () => void;
  dismissLabel?: string;
  /** Section scope: classes for the fallback card instead of the inline default (e.g. panel-shaped). */
  fallbackClassName?: string;
  /** Section scope: a crashed section retries by itself when this changes (new data, another artifact version). */
  resetKey?: unknown;
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

  componentDidUpdate(prev: Props) {
    if (this.state.failed && !Object.is(prev.resetKey, this.props.resetKey)) this.reset();
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
      // Like the answer's error line (QA-A23) and ChatGPT/Claude: a quiet inline notice, small icon, no filled card or
      // border, and plain text controls rather than pill buttons.
      const link = 'font-medium text-fg underline-offset-2 hover:underline focus-visible:underline';
      return (
        <div role="alert" data-testid="section-crash" data-section={this.props.name} className={this.props.fallbackClassName ?? 'my-2 font-sans text-sm text-fg-muted'}>
          <p className="flex items-start gap-2">
            <AlertTriangle size={15} aria-hidden className="mt-0.5 shrink-0 text-danger" />
            <span>{chunk ? "This part couldn't load. Check your connection." : "This part couldn't be displayed."}{rec && <span className="ml-1 text-xs text-fg-subtle">Ref {rec.id}</span>}</span>
          </p>
          <p className="mt-1 flex items-center gap-3 pl-[23px] text-xs">
            <button type="button" onClick={this.reset} className={link}>Try again</button>
            {this.props.onDismiss && (
              <button type="button" onClick={this.props.onDismiss} className={link}>{this.props.dismissLabel ?? 'Close'}</button>
            )}
          </p>
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
          <button type="button" autoFocus onClick={() => window.location.reload()} className="rounded-md bg-fg px-4 py-2 text-sm font-medium text-bg hover:opacity-90">
            Reload
          </button>
          <button type="button" onClick={this.newChat} className="rounded-md border border-line px-4 py-2 text-sm font-medium hover:bg-hover">
            Start a new chat
          </button>
        </div>
        {rec && (
          <details className="mt-2 w-full max-w-md text-left text-xs text-fg-subtle">
            <summary className="cursor-pointer text-center">Reference {rec.id} · diagnostics for support</summary>
            <p className="mt-2 text-fg-muted">Includes no message text or patient data. Below is exactly what gets copied.</p>
            <pre data-testid="crash-diagnostics" className="mt-2 max-h-56 overflow-auto whitespace-pre-wrap rounded-lg border border-line bg-muted p-2 font-mono text-[11px] leading-snug">{diag}</pre>
            <div className="mt-2 flex items-center gap-2">
              <button type="button" onClick={this.copy} className="font-medium text-fg underline-offset-2 hover:underline focus-visible:underline">
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
