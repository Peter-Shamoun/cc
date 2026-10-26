import { useSyncExternalStore } from 'react';

const subscribe = (cb: () => void) => {
  window.addEventListener('online', cb);
  window.addEventListener('offline', cb);
  return () => { window.removeEventListener('online', cb); window.removeEventListener('offline', cb); };
};

/** Browser connectivity (navigator.onLine + online/offline events), for an offline banner or composer hint. */
export function useOnline(): boolean {
  return useSyncExternalStore(subscribe, () => navigator.onLine, () => true);
}

/** A settled-connectivity store: offline is published after `settleMs` of continuous offline, online at once. */
function settledStore(settleMs: number) {
  const listeners = new Set<() => void>();
  let settledOffline = typeof navigator !== 'undefined' && navigator.onLine === false; // loaded offline: show now
  let timer: ReturnType<typeof setTimeout> | undefined;
  const publish = (v: boolean) => { if (settledOffline !== v) { settledOffline = v; listeners.forEach((l) => l()); } };
  const onChange = () => {
    clearTimeout(timer);
    if (navigator.onLine) publish(false);
    else timer = setTimeout(() => publish(true), settleMs);
  };
  return {
    subscribe(cb: () => void) {
      if (!listeners.size) { window.addEventListener('online', onChange); window.addEventListener('offline', onChange); }
      listeners.add(cb);
      return () => {
        listeners.delete(cb);
        if (!listeners.size) { clearTimeout(timer); window.removeEventListener('online', onChange); window.removeEventListener('offline', onChange); }
      };
    },
    // Back online counts immediately, even before the 'online' handler runs.
    // With nobody subscribed no events are tracked, so a fresh mount takes the current state (loaded offline: show now).
    get: () => { if (!listeners.size) settledOffline = !navigator.onLine; return navigator.onLine || !settledOffline; },
  };
}
const stores = new Map<number, ReturnType<typeof settledStore>>();

/**
 * useOnline with hysteresis for UI chrome (the offline banner): going offline is reported only after it has lasted
 * `settleMs`, so wifi handoffs and sub-second blips don't flash a banner; coming back is reported at once, so a stale
 * "You're offline" never sits over a working connection. A page that loads offline shows it immediately.
 */
export function useSettledOnline(settleMs = 1000): boolean {
  let store = stores.get(settleMs);
  if (!store) { store = settledStore(settleMs); stores.set(settleMs, store); }
  return useSyncExternalStore(store.subscribe, store.get, () => true);
}
