/**
 * Coalesce many updates into one per animation frame. Stream deltas arrive far faster than the
 * screen refreshes; painting each one wastes main-thread time and makes long answers stutter.
 * `schedule()` queues one `flush` for the next frame (setTimeout(16) when rAF is unavailable or the
 * tab is hidden, where rAF is paused); `now()` flushes at once and drops the queued frame.
 */
export function frameBatch(flush: () => void) {
  let cancel: (() => void) | null = null;
  const run = () => { cancel = null; flush(); };
  return {
    schedule() {
      if (cancel) return;
      if (typeof requestAnimationFrame === 'function' && !(typeof document !== 'undefined' && document.hidden)) {
        const id = requestAnimationFrame(run);
        cancel = () => cancelAnimationFrame(id);
      } else {
        const id = setTimeout(run, 16);
        cancel = () => clearTimeout(id);
      }
    },
    now() {
      cancel?.();
      cancel = null;
      flush();
    },
    get pending() { return cancel !== null; },
  };
}
