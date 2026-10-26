import { useEffect, useRef, useState } from 'react';

/**
 * Streaming text pacing (Claude/ChatGPT fidelity). Servers flush whole sentences (the demo sends a paragraph in one
 * delta), so text arrives in jumps. Both references reveal it as an even flow instead. We pace only the text that
 * arrives after mount while a part is streaming: each frame shows backlog × dt / DRAIN_MS (a big burst eases out
 * in well under a second), never slower than MIN_RATE (250 chars/s, so short tails don't crawl), snapping to word ends so words never appear half-typed. Text present at mount
 * of a finished part (reopening a chat, history) shows at once; reduced motion shows everything at once; a shrink (regenerate) resets.
 */
export const DRAIN_MS = 200;
export const MIN_RATE = 0.25; // chars per ms
const SNAP = 12;

/** How many characters of `target` to show after `dtMs`, given `shown` were visible. Pure, for tests. */
export function nextReveal(shown: number, target: string, dtMs: number): number {
  const len = target.length;
  if (shown >= len) return len;
  const backlog = len - shown;
  let n = Math.min(len, shown + Math.max(Math.ceil(MIN_RATE * Math.max(dtMs, 1)), Math.ceil((backlog * Math.max(dtMs, 1)) / DRAIN_MS)));
  // Finish the word we are in (if it ends soon) rather than cutting it.
  for (let i = n, end = Math.min(len, n + SNAP); i < end; i++) {
    if (/\s/.test(target[i])) { n = i; break; }
    if (i === end - 1 && end === len) n = len;
  }
  return n;
}

const reduced = () => typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
const raf: (cb: (t: number) => void) => number = (cb) =>
  typeof requestAnimationFrame === 'function' ? requestAnimationFrame(cb) : (setTimeout(() => cb(performance.now()), 16) as unknown as number);
const caf = (id: number) => (typeof cancelAnimationFrame === 'function' ? cancelAnimationFrame(id) : clearTimeout(id));

/** Returns the visible prefix of `text` and whether more is still to come on screen. */
export function usePacedText(text: string, streaming: boolean, liveTurn = false): { shown: string; pending: boolean } {
  // Paced only once this part has been seen streaming, or when it mounts inside a turn that was streaming on screen
  // (`liveTurn`): a fast server can deliver text.start…message.end in one network chunk, so the part's first render
  // is already closed, yet the reader never saw it, so it still flows in. A part that mounts mid-stream starts from nothing.
  const [live, setLive] = useState(streaming || liveTurn);
  const [n, setN] = useState(streaming || liveTurn ? 0 : text.length);
  if ((streaming || liveTurn) && !live) setLive(true);
  const off = !live || reduced();
  // Adjust during render (React's "storing information from previous renders" pattern), not in an effect:
  // pacing off or the text shrank (regenerate) → everything shows and later growth paces from here.
  if ((off && n !== text.length) || n > text.length) setN(text.length);
  const count = off ? text.length : Math.min(n, text.length);
  const behind = count < text.length;

  const latest = useRef(text);
  useEffect(() => { latest.current = text; }, [text]);
  useEffect(() => {
    if (!behind) return;
    let last = performance.now();
    let id = 0;
    const tick = (t: number) => {
      const dt = Math.max(1, t - last);
      last = t;
      setN((cur) => nextReveal(Math.min(cur, latest.current.length), latest.current, dt));
      id = raf(tick); // cancelled by the cleanup once caught up (behind turns false)
    };
    id = raf(tick);
    return () => caf(id);
  }, [behind]);

  return { shown: behind ? text.slice(0, count) : text, pending: behind };
}
