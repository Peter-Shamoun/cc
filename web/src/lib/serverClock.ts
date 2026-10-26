/**
 * The server's clock, seen from here. Deadlines the server sends (a download's or clarification's `expiresAt`, a
 * Retry-After HTTP date) are in server time; comparing them to a skewed local clock expires cards early or late and
 * retries a rate limit too soon or far too late. Every API response's `Date` header updates the offset. It is only
 * applied past SKEW_MS, because `Date` has 1-second resolution and the request itself takes time.
 */
const SKEW_MS = 2000;
let offset = 0;

/** Record a response's `Date` header (server time when it was sent). Garbled or absent headers are ignored. */
export function noteServerDate(date: string | null | undefined, localNow = Date.now()): void {
  if (!date) return;
  const at = Date.parse(date);
  if (Number.isNaN(at)) return;
  const d = at - localNow;
  offset = Math.abs(d) > SKEW_MS ? d : 0;
}

/** Now, in server time (milliseconds since the epoch). */
export function serverNow(localNow = Date.now()): number { return localNow + offset; }

/** Current skew estimate, ms (server minus local); exposed for tests and diagnostics. */
export function serverSkewMs(): number { return offset; }

/** Tests only. */
export function resetServerClock(): void { offset = 0; }
