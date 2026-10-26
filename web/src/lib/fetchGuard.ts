/**
 * Non-stream API calls (conversation list/search/detail, shares, feedback, capabilities): two edge cases the chat
 * stream already handles.
 *
 * - **429 + Retry-After.** The server did nothing, so the same request can go again. `fetchRetrying` waits out
 *   Retry-After (delay-seconds or an HTTP date measured against the response's own `Date`), up to `maxWaitMs`, at most
 *   `maxRetries` times; past that the 429 is returned for the caller's normal error path.
 * - **Oversized bodies.** `readJsonCapped` refuses a body over `maxBytes`, by Content-Length up front or by counting
 *   while reading, before parsing, so one runaway response can't freeze or crash the tab.
 */
import { serverNow } from './serverClock';

/** Retry-After as delay-seconds or an HTTP date (RFC 9110 §10.2.3), in ms; null if absent/garbled. */
export function retryAfterMs(value: string | null, now = serverNow(), serverDate?: string | null): number | null {
  if (!value) return null;
  const v = value.trim();
  if (/^\d+$/.test(v)) return Number(v) * 1000;
  if (!/[a-z]/i.test(v)) return null; // "1.5", "-3": not delay-seconds and not a date (Date.parse would guess one)
  const at = Date.parse(v);
  if (Number.isNaN(at)) return null;
  const ref = serverDate ? Date.parse(serverDate) : NaN;
  return Math.max(0, at - (Number.isNaN(ref) ? now : ref));
}

/** Retry-After of a response, measured against that response's own `Date`. */
export const retryAfterOf = (res: Response) => retryAfterMs(res.headers.get('retry-after'), serverNow(), res.headers.get('date'));

export interface RetryOpts { maxRetries?: number; maxWaitMs?: number; sleep?: (ms: number, signal?: AbortSignal | null) => Promise<void> }

const defaultSleep = (ms: number, signal?: AbortSignal | null) => new Promise<void>((resolve, reject) => {
  if (signal?.aborted) { reject(new DOMException('aborted', 'AbortError')); return; }
  const t = setTimeout(() => { signal?.removeEventListener('abort', on); resolve(); }, ms);
  const on = () => { clearTimeout(t); reject(new DOMException('aborted', 'AbortError')); };
  signal?.addEventListener('abort', on, { once: true });
});

export async function fetchRetrying(fetchImpl: typeof fetch, input: string, init: RequestInit = {}, o: RetryOpts = {}): Promise<Response> {
  const { maxRetries = 2, maxWaitMs = 10_000, sleep = defaultSleep } = o;
  for (let attempt = 0; ; attempt++) {
    const res = await fetchImpl(input, init);
    if (res.status !== 429 || attempt >= maxRetries) return res;
    const wait = retryAfterOf(res) ?? 500 * 2 ** attempt;
    if (wait > maxWaitMs) return res;
    await sleep(wait, init.signal);
  }
}

export class ResponseTooLargeError extends Error {
  constructor(readonly maxBytes: number) { super('response_too_large'); this.name = 'ResponseTooLargeError'; }
}

/** Response JSON, refusing bodies over `maxBytes` (default 32 MiB) without buffering them. */
export async function readJsonCapped<T>(res: Response, maxBytes = 32 * 1024 * 1024): Promise<T> {
  const declared = Number(res.headers.get('content-length'));
  if (Number.isFinite(declared) && declared > maxBytes) { res.body?.cancel().catch(() => {}); throw new ResponseTooLargeError(maxBytes); }
  if (!res.body) return (await res.json()) as T;
  const reader = res.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    total += value.byteLength;
    if (total > maxBytes) { reader.cancel().catch(() => {}); throw new ResponseTooLargeError(maxBytes); }
    chunks.push(value);
  }
  const all = new Uint8Array(total);
  let off = 0;
  for (const c of chunks) { all.set(c, off); off += c.byteLength; }
  return JSON.parse(new TextDecoder().decode(all)) as T;
}
