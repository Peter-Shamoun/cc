import { useEffect, type RefObject } from 'react';

const reduced = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches;

/**
 * Phone drawer gestures (ChatGPT/Claude mobile): drag the open drawer (or its scrim) left to close it, following
 * the finger; release past 35% of its width, or with a quick flick, to close, otherwise it springs back.
 * Touch/pen only (mouse users have the X and the scrim). Vertical scrolling of the chat list is untouched
 * (`touch-action: pan-y` on the drawer; we only take over once the move is clearly horizontal).
 */
export function useSwipeToClose(root: RefObject<HTMLElement | null>, active: boolean, onClose: () => void) {
  useEffect(() => {
    const el = root.current;
    if (!active || !el) return;
    const panel = () => el.querySelector<HTMLElement>('[data-testid="sidebar"]');
    const scrim = () => el.querySelector<HTMLElement>('[data-testid="drawer-scrim"]');
    let start: { x: number; y: number; t: number; id: number } | null = null;
    let dragging = false;
    let dx = 0;
    const set = (x: number, animate: boolean) => {
      const p = panel(), s = scrim();
      if (!p) return;
      const w = p.offsetWidth || 300;
      const t = animate && !reduced() ? 'transform 180ms var(--ease-out, ease-out)' : 'none';
      p.style.transition = t;
      p.style.transform = x ? `translateX(${x}px)` : '';
      if (s) { s.style.transition = animate && !reduced() ? 'opacity 180ms ease-out' : 'none'; s.style.opacity = x ? String(Math.max(0, 1 + x / w)) : ''; }
    };
    const down = (e: PointerEvent) => {
      if (e.pointerType === 'mouse' || !e.isPrimary) return;
      start = { x: e.clientX, y: e.clientY, t: e.timeStamp, id: e.pointerId };
      dragging = false; dx = 0;
    };
    const move = (e: PointerEvent) => {
      if (!start || e.pointerId !== start.id) return;
      const mx = e.clientX - start.x, my = e.clientY - start.y;
      if (!dragging) {
        if (Math.abs(mx) < 10 && Math.abs(my) < 10) return;
        if (mx >= 0 || Math.abs(my) > Math.abs(mx)) { start = null; return; } // a scroll or a rightward drag: not ours
        dragging = true;
        try { el.setPointerCapture(e.pointerId); } catch { /* jsdom */ }
      }
      dx = Math.min(0, mx);
      set(dx, false);
    };
    const up = (e: PointerEvent) => {
      if (!start || e.pointerId !== start.id) return;
      const s = start;
      start = null;
      if (!dragging) return;
      dragging = false;
      const w = panel()?.offsetWidth || 300;
      const v = dx / Math.max(1, e.timeStamp - s.t); // px/ms, negative = leftward
      if (dx < -w * 0.35 || v < -0.5) {
        if (reduced()) { onClose(); return; }
        set(-w, true);
        window.setTimeout(onClose, 170);
      } else set(0, true);
    };
    const cancel = () => { if (dragging) set(0, true); start = null; dragging = false; };
    // A tap on the scrim after a drag shouldn't also count as a click.
    const click = (e: MouseEvent) => { if (dx < -10) { e.stopPropagation(); e.preventDefault(); dx = 0; } };
    el.addEventListener('pointerdown', down);
    el.addEventListener('pointermove', move);
    el.addEventListener('pointerup', up);
    el.addEventListener('pointercancel', cancel);
    el.addEventListener('click', click, true);
    return () => {
      el.removeEventListener('pointerdown', down);
      el.removeEventListener('pointermove', move);
      el.removeEventListener('pointerup', up);
      el.removeEventListener('pointercancel', cancel);
      el.removeEventListener('click', click, true);
    };
  }, [root, active, onClose]);
}

/** Phone: a rightward swipe that starts at the left screen edge (AppShell renders a thin pan-y strip there) opens the drawer. */
export function useEdgeSwipeToOpen(active: boolean, onOpen: () => void, edge = 24) {
  useEffect(() => {
    if (!active) return;
    let start: { x: number; y: number; id: number } | null = null;
    const down = (e: PointerEvent) => {
      if (e.pointerType === 'mouse' || !e.isPrimary || e.clientX > edge) return;
      start = { x: e.clientX, y: e.clientY, id: e.pointerId };
    };
    const up = (e: PointerEvent) => {
      if (!start || e.pointerId !== start.id) return;
      const dx = e.clientX - start.x, dy = Math.abs(e.clientY - start.y);
      start = null;
      if (dx > 60 && dy < dx * 0.6) onOpen();
    };
    const cancel = () => { start = null; };
    window.addEventListener('pointerdown', down, true);
    window.addEventListener('pointerup', up, true);
    window.addEventListener('pointercancel', cancel, true);
    return () => {
      window.removeEventListener('pointerdown', down, true);
      window.removeEventListener('pointerup', up, true);
      window.removeEventListener('pointercancel', cancel, true);
    };
  }, [active, onOpen, edge]);
}
