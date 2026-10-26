/**
 * Warm the lazy chunks the first answer needs (Markdown renderer, KaTeX, Vega) while the user is still reading or
 * typing, one per idle slot, so parsing and evaluating ~1 MB of JS never lands in the middle of a streaming answer.
 * On a 6x-throttled CPU that was a 190–360 ms long task mid-stream, with keystrokes stuck behind it.
 * Skipped on Save-Data and 2G connections (the chunks then load on first use, as before).
 */
type Idle = (cb: () => void) => unknown;
type Conn = { saveData?: boolean; effectiveType?: string } | undefined;

export const browserIdle: Idle = (cb) =>
  typeof window !== 'undefined' && 'requestIdleCallback' in window ? window.requestIdleCallback(cb, { timeout: 3000 }) : setTimeout(cb, 1500);

export function shouldPrefetch(conn: Conn): boolean {
  if (!conn) return true;
  return !conn.saveData && !/(^|-)2g$/.test(conn.effectiveType ?? '');
}

/** Runs `tasks` in order, each in its own idle callback; a failed task (offline, chunk 404) doesn't stop the rest. */
export function scheduleIdlePrefetch(tasks: (() => Promise<unknown>)[], { idle = browserIdle, conn }: { idle?: Idle; conn?: Conn } = {}): void {
  // The first task (the Markdown renderer) always runs: every answer needs it.
  const list = shouldPrefetch(conn) ? tasks : tasks.slice(0, 1);
  const next = (i: number) => {
    if (i >= list.length) return;
    idle(() => { list[i]().catch(() => undefined).finally(() => next(i + 1)); });
  };
  next(0);
}
