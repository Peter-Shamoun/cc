/**
 * The one sanctioned sink for client errors. Every record goes through safeError() and redact() first. Context is
 * allowlisted, and nothing is persisted: there's an in-memory ring only, with no storage, URL or console.
 *
 * A real backend plugs in with setReporter(fn). fn only ever receives redacted ErrorRecords, so it can POST them
 * as-is. In production the global handlers call preventDefault(), so the browser doesn't print the raw error
 * (whose message may hold PHI) to the console.
 */
import { redact, safeError, type SafeError } from './redact';

export interface ErrorRecord extends SafeError {
  id: string;
  at: number;
  kind: 'render' | 'window' | 'promise' | 'handled';
  context: Partial<Record<ContextKey, string | number>>;
}
const CONTEXT_KEYS = ['component', 'action', 'code', 'status'] as const;
type ContextKey = (typeof CONTEXT_KEYS)[number];
export type Reporter = (r: ErrorRecord) => void;

const RING = 20;
const recent: ErrorRecord[] = [];
let reporter: Reporter | null = null;
let seq = 0;

export function setReporter(fn: Reporter | null): void {
  reporter = fn;
}

export function recentErrors(): readonly ErrorRecord[] {
  return recent;
}

export function reportError(e: unknown, kind: ErrorRecord['kind'] = 'handled', context: Record<string, unknown> = {}): ErrorRecord {
  const ctx: ErrorRecord['context'] = {};
  for (const k of CONTEXT_KEYS) {
    const v = context[k];
    if (typeof v === 'number') ctx[k] = v;
    else if (typeof v === 'string') ctx[k] = redact(v, 60);
  }
  const record: ErrorRecord = { ...safeError(e), id: `e${Date.now().toString(36)}${(seq++).toString(36)}`, at: Date.now(), kind, context: ctx };
  recent.push(record);
  if (recent.length > RING) recent.shift();
  try {
    reporter?.(record);
  } catch {
    /* a failing reporter must never take the app down */
  }
  return record;
}

let installed = false;
/** Install window error / unhandledrejection capture. Idempotent; `quiet` suppresses the browser's raw console print. */
export function installGlobalErrorHandlers(target: Window = window, quiet = import.meta.env.PROD): void {
  if (installed) return;
  installed = true;
  target.addEventListener('error', (ev: ErrorEvent) => {
    reportError(ev.error ?? ev.message, 'window');
    if (quiet) ev.preventDefault();
  });
  target.addEventListener('unhandledrejection', (ev: PromiseRejectionEvent) => {
    if ((ev.reason as { name?: string } | null)?.name === 'AbortError') return; // user cancelled; not an error
    reportError(ev.reason, 'promise');
    if (quiet) ev.preventDefault();
  });
}

/** Test-only reset. */
export function _resetErrorReporting(): void {
  recent.length = 0;
  reporter = null;
  installed = false;
}
