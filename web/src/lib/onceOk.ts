/**
 * Memoize an async loader (a lazy chunk import) on success only. A rejected import (offline, flaky network, a chunk
 * swapped by a deploy) is forgotten, so the next caller tries again instead of getting the cached failure forever.
 */
export function onceOk<T>(load: () => Promise<T>): () => Promise<T> {
  let p: Promise<T> | null = null;
  return () => (p ??= load().catch((e: unknown) => { p = null; throw e; }));
}
