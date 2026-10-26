import { useEffect, useState } from 'react';
import { serverNow } from './serverClock';

/**
 * True once `expiresAt` (ISO time) has passed. Re-renders at the deadline, so a card that is on screen when its
 * download or clarification expires flips to the expired state (a render-time `Date.now()` check never did).
 * `expiresAt` is server time, so it is compared with the server clock estimate (lib/serverClock), not the raw local
 * clock. No `Date.now()` during render: the initial check is a lazy state initializer, later checks are a timer.
 */
export function useExpired(expiresAt?: string | null): boolean {
  const at = expiresAt ? Date.parse(expiresAt) : NaN;
  // Holds the deadline that has passed; a later (renewed) deadline is not expired until its own timer fires.
  const [passed, setPassed] = useState<number | null>(() => (Number.isFinite(at) && at <= serverNow() ? at : null));
  useEffect(() => {
    if (!Number.isFinite(at)) return;
    const t = setTimeout(() => setPassed(at), Math.min(Math.max(0, at - serverNow()), 2 ** 31 - 1));
    return () => clearTimeout(t);
  }, [at]);
  return Number.isFinite(at) && passed === at;
}
