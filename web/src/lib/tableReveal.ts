/**
 * Partial-table streaming (ChatGPT/Claude parity). Their tables grow row by row while the answer
 * streams. Our contract sends a table as one `part`, so a big result used to appear all at once and
 * push the layout down in one jump. While the answer is still streaming, this view-only transform
 * shows a table's rows a few at a time over at most `maxFrames` animation frames (about 400 ms).
 * Persisted messages, reloads and `message.end` always carry the full rows. Users who ask for
 * reduced motion get the whole table at once.
 */
import type { Part } from '../api/contract';

export interface TableRevealOptions {
  /** Tables with at most this many rows appear whole. */
  minRows: number;
  /** Upper bound on frames a reveal takes, so 500 rows don't crawl. */
  maxFrames: number;
}

export function createTableReveal(opts: Partial<TableRevealOptions> = {}) {
  const { minRows = 3, maxFrames = 24 } = opts;
  /** partId -> rows shown so far (only tables still revealing). */
  const shown = new Map<string, number>();
  /** Tables already seen (revealed or too small), so a re-sent part doesn't restart. */
  const seen = new Set<string>();
  let total = new Map<string, number>();

  const stepOf = (n: number) => Math.max(1, Math.ceil(n / maxFrames));

  return {
    /** Register tables that just arrived. Returns true if any reveal is in progress. */
    note(parts: readonly Part[]): boolean {
      for (const p of parts) {
        if (p.type !== 'table' || seen.has(p.id)) continue;
        seen.add(p.id);
        if (p.rows.length > minRows) shown.set(p.id, stepOf(p.rows.length));
      }
      total = new Map(parts.filter((p) => p.type === 'table').map((p) => [p.id, (p as { rows: unknown[] }).rows.length]));
      return shown.size > 0;
    },
    /** Advance one frame. Returns true while any table still has hidden rows. */
    step(): boolean {
      for (const [id, n] of shown) {
        const all = total.get(id) ?? n;
        const next = n + stepOf(all);
        if (next >= all) shown.delete(id);
        else shown.set(id, next);
      }
      return shown.size > 0;
    },
    /** The parts as they should paint now (same array if nothing is being revealed). */
    apply<P extends Part>(parts: P[]): P[] {
      if (shown.size === 0) return parts;
      return parts.map((p) => (p.type === 'table' && shown.has(p.id) ? { ...p, rows: p.rows.slice(0, shown.get(p.id)) } : p));
    },
    /** Show everything (end of stream, Stop). */
    finish() { shown.clear(); },
    get active() { return shown.size > 0; },
  };
}

export function prefersReducedMotion(): boolean {
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function' && window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}
