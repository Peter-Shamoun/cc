import { noteServerDate, serverNow } from '../lib/serverClock';
import { getShare, loadShared, revokeShare, setShare } from '../contract/share';
import type { ShareLink, SharedConversation } from '../contract/v1.gen';
import type { ErrorPart, StreamEvent } from '../lib/contract';
import { asSession, type AuthSession } from '../auth/session';
import type { HttpError } from '../api/contract';
import type { AnalystAdapter, AnyStreamEvent } from './types';
import { loadCapabilities } from '../contract/capabilities';
import { exportUrl, exportFilename as serverExportFilename, type ExportFormat } from '../contract/sources';
import type { Capabilities, Feedback, FeedbackRequest } from '../contract/v1.gen';
import { sendFeedback } from '../contract/feedback';
import { SseDecoder, readSse } from '../contract/sse';

/**
 * Adapter for the real analyst endpoint (spec §5). POST {base}/api/chat with
 * {conversationId, messages}; response is text/event-stream with one JSON
 * StreamEvent per `data:` line. Auth is an httpOnly cookie (credentials:
 * 'same-origin'); nothing PHI-bearing goes in a URL.
 */
export interface HttpAdapterOptions {
  /** Reconnect attempts after a dropped stream (Last-Event-ID replay). */
  maxRetries: number;
  retryBaseMs: number;
  /** After Stop, how long to keep reading for the server's aborted message.end (with usage). */
  stopGraceMs: number;
  /** How long a dropped stream waits for the browser to come back online before giving up. */
  offlineWaitMs: number;
  /** 429 before the answer starts: how many times to wait out Retry-After and try again on our own. */
  maxRateRetries: number;
  /** Longest Retry-After we'll wait on our own; longer (or missing past the cap) falls through to the error card. */
  maxRateWaitMs: number;
  /**
   * A stream that sends nothing (not even the server's 15-s heartbeat) for this long is treated as dropped: the
   * connection is closed and, once the answer has started, re-attached with Last-Event-ID. Before it starts, the
   * answer ends with a retryable "timed out" card instead of a spinner that never stops.
   */
  idleTimeoutMs: number;
  /** One SSE line (one event) larger than this is refused instead of buffering until the tab runs out of memory. */
  maxFrameBytes: number;
  /** Demo-only fault injection (dev query param ?fault=429): the first attempt asks the demo API for a 429. */
  demoFault?: 'rate_limited';
}

/** Retry-After as delay-seconds or an HTTP date (RFC 9110 §10.2.3), in ms; null if absent/garbled. */
/** CONTRACT.md §8: the server has nothing to replay (run finished > 60 s ago or never existed). */
async function errorCode(res: Response): Promise<string | undefined> {
  try { return ((await res.clone().json()) as { error?: { code?: string } })?.error?.code; } catch { return undefined; }
}

export function streamGone(): Error {
  const e = new Error('stream_gone');
  e.name = 'StreamGoneError';
  return e;
}

/**
 * An HTTP-date is server time, so it is measured against the same response's `Date` header (RFC 9110 §10.2.3); without
 * one, against the server clock estimate. The local clock alone would retry at once when it runs fast, or wait
 * (or give up) far too long when it runs slow.
 */
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
const retryAfterOf = (res: Response) => retryAfterMs(res.headers.get('retry-after'), serverNow(), res.headers.get('date'));

const abortError = () => new DOMException('aborted', 'AbortError');
const isOffline = () => typeof navigator !== 'undefined' && navigator.onLine === false;

/** The request never reached the server (offline, DNS, CORS, server down). */
export function networkError(): ErrorPart {
  return isOffline()
    ? { type: 'error', id: 'err-offline', code: 'offline', title: "You're offline", message: 'Check your connection, then retry.', retryable: true }
    : { type: 'error', id: 'err-network', code: 'internal', title: "Can't reach the analyst", message: 'The server did not respond. Try again in a moment.', retryable: true };
}

type Kind = { title: string; message: string; retryable: boolean };
/** Our copy per known `HttpError.code` (contract `$defs/ErrorCode`, open). Server messages are preferred when present. */
export const HTTP_ERROR_COPY: Record<string, Kind> = {
  auth_expired: { title: 'Session expired', message: 'Sign in again to continue.', retryable: false },
  rate_limited: { title: 'Too many requests', message: 'Wait a moment, then retry.', retryable: true },
  access_denied: { title: 'Not available', message: "You don't have access to this conversation.", retryable: false },
  not_found: { title: 'Not found', message: "That conversation or answer doesn't exist any more. Start a new question.", retryable: false },
  validation: { title: "Couldn't send that", message: 'Check the question and try again.', retryable: false },
  payload_too_large: { title: 'Message too long', message: 'Shorten the message, then send it again.', retryable: false },
  clarification_expired: { title: 'Ask again', message: 'That follow-up question has expired. Ask your question again.', retryable: false },
  conflict: { title: 'Ask again', message: 'This conversation changed in the meantime. Ask your question again.', retryable: false },
  method_not_allowed: { title: 'Not supported', message: "This server doesn't support that action yet.", retryable: false },
  invalid_title: { title: "Couldn't rename", message: 'Use a shorter title without line breaks.', retryable: false },
  transcripts_unavailable: { title: 'Chat unavailable', message: 'This chat is no longer available.', retryable: false },
  stream_gone: { title: 'Answer no longer streaming', message: 'Reload the conversation to see the saved answer.', retryable: false },
  internal: { title: 'Analyst unavailable', message: 'Something went wrong on our side. Try again in a moment.', retryable: true },
};
/** Fallback when the body has no known code (old server, proxy page, HTML error). */
const BY_STATUS: Record<number, string> = {
  400: 'validation', 401: 'auth_expired', 403: 'access_denied', 404: 'not_found', 405: 'method_not_allowed', 409: 'conflict', 410: 'transcripts_unavailable',
  413: 'payload_too_large', 422: 'validation', 429: 'rate_limited',
};
const MAX_SERVER_MESSAGE = 300;

/**
 * Map a non-stream HTTP failure (CONTRACT.md §2: `{"error": {code, message, legacyCode?, maxBytes?}}`) to an error part.
 * Resolution order: `error.code` if we know it, then `legacyCode`, then the HTTP status. 5xx and unknown statuses are
 * retryable; an unknown 4xx is not (resending the same request won't help).
 * Server messages are fixed, PHI-free strings. We still cap them and fall back to our own copy per code.
 */
export async function httpError(res: Response): Promise<ErrorPart> {
  let body: Partial<HttpError['error']> = {};
  try {
    const j = (await res.json()) as Partial<HttpError> | null;
    if (j && typeof j.error === 'object' && j.error) body = j.error;
  } catch { /* not JSON */ }
  const str = (v: unknown) => (typeof v === 'string' ? v : '');
  const raw = str(body.code);
  const legacy = str(body.legacyCode);
  const code = HTTP_ERROR_COPY[raw] ? raw : HTTP_ERROR_COPY[legacy] ? legacy : BY_STATUS[res.status] ?? (res.status >= 500 || res.status < 400 ? 'internal' : '');
  const serverMsg = str(body.message).trim().slice(0, MAX_SERVER_MESSAGE);
  const base = { type: 'error' as const, id: `err-http-${res.status}` };
  if (!code) {
    // Unknown 4xx with an unknown code: not retryable, keep the server's code for diagnostics.
    return { ...base, code: raw || `http_${res.status}`, title: 'Request not accepted', message: serverMsg || `The analyst rejected the request (${res.status}).`, retryable: false };
  }
  const k = HTTP_ERROR_COPY[code];
  if (code === 'internal') {
    // Generic 5xx copy only; a server-side message for an internal error is not shown.
    return { ...base, code: raw && !HTTP_ERROR_COPY[raw] ? raw : 'internal', title: k.title, message: `The analyst returned ${res.status}. Try again in a moment.`, retryable: true };
  }
  let message = serverMsg || k.message;
  if (code === 'payload_too_large' && !serverMsg && typeof body.maxBytes === 'number' && body.maxBytes > 0) {
    message = `Shorten the message to under ${Math.max(1, Math.floor(body.maxBytes / 1024))} KB, then send it again.`;
  }
  const retryAfter = retryAfterOf(res) ?? 0;
  return { ...base, code, title: k.title, message, retryable: k.retryable, ...(code === 'rate_limited' && retryAfter > 0 ? { retryAfterMs: retryAfter } : {}) };
}

export class HttpAnalystAdapter implements AnalystAdapter {
  readonly name = 'http';
  private current: string | null = null;
  /**
   * `auth`: an AuthSession (src/auth/session.ts). A bare string is a demo token (`demo:<customer>:<user>`).
   * Real deployments use cookieSession(): HttpOnly `cc_session` cookie plus the CSRF header.
   */
  constructor(private base = '', auth?: string | AuthSession, opts: Partial<HttpAdapterOptions> = {}) {
    this.auth = asSession(auth);
    this.opts = { maxRetries: 4, retryBaseMs: 500, stopGraceMs: 1500, offlineWaitMs: 45_000, maxRateRetries: 2, maxRateWaitMs: 30_000, idleTimeoutMs: 45_000, maxFrameBytes: 32 * 1024 * 1024, ...opts };
  }
  private readonly opts: HttpAdapterOptions;
  private readonly auth: AuthSession;

  private headers(extra: Record<string, string> = {}): Record<string, string> {
    return { ...extra, ...this.auth.headers() };
  }

  /** Server-side cancel (POST /api/chat/{messageId}/stop); the fetch abort alone only drops the socket. */
  async stop(): Promise<void> {
    if (!this.current) return;
    await fetch(`${this.base}/api/chat/${encodeURIComponent(this.current)}/stop`, { method: 'POST', credentials: 'same-origin', headers: this.headers() }).catch(() => undefined);
  }

  async *streamTurn({ conversationId, messages, signal, resume, regenerate, options, onConnection }: Parameters<AnalystAdapter['streamTurn']>[0]): AsyncIterable<AnyStreamEvent> {
    // The server takes user turns only; prior assistant prose is reconstructed server-side.
    const users = messages.filter((m) => m.role === 'user').map(({ id, role, text, parentId, editOf }) => ({ id, role, text, ...(parentId ? { parentId } : {}), ...(editOf ? { editOf } : {}) }));
    // Strategy/model picks (contract 1.4.0); omitted when everything is the server default.
    const opt = options && Object.keys(options).length ? { options } : {};
    let body = JSON.stringify({ conversationId, messages: users, ...(resume ? { resume } : {}), ...(regenerate ? { regenerate } : {}), ...opt });
    let regenerateFallback = !!regenerate;
    // `signal` is the user's Stop. We keep reading after it fires so the server's
    // `message.end {finishReason: 'aborted', usage}` still arrives (CONTRACT.md §8: within 1 s);
    // the socket itself is only dropped after STOP_GRACE_MS.
    const wire = new AbortController();
    let graceTimer: ReturnType<typeof setTimeout> | undefined;
    const onAbort = () => {
      void this.stop();
      graceTimer = setTimeout(() => wire.abort(), this.opts.stopGraceMs);
      if (!this.current) wire.abort(); // nothing started server-side yet: nothing to wait for
    };
    if (signal.aborted) throw new DOMException('aborted', 'AbortError');
    signal.addEventListener('abort', onAbort, { once: true });
    let messageId: string | null = null;
    let lastSeq = -1;
    let retries = 0;
    let dropped = false; // between a drop and the first replayed event
    let resumeFallback = !!resume;
    let rateRetries = 0;
    let first = true;
    let useGet = true;
    try {
      for (;;) {
        // Reconnect (CONTRACT.md §8): same POST, plus Last-Event-ID so the server replays only what we missed.
        const replay: Record<string, string> = messageId !== null ? { 'last-event-id': `${messageId}:${lastSeq}` } : {};
        if (first && this.opts.demoFault) replay['x-demo-fault'] = this.opts.demoFault; // first attempt only, so the retry gets through
        first = false;
        let res: Response;
        // Contract 1.1: re-attach with GET /api/chat/{id}/stream (preferred); a 1.0 server without that
        // route gets the old POST replay. The server keeps the run alive ~10 s after a drop, which the
        // 250 ms / 1 s / 2 s / 4 s backoff fits inside.
        const viaGet = messageId !== null && useGet;
        try {
          res = viaGet
            ? await fetch(`${this.base}/api/chat/${encodeURIComponent(messageId!)}/stream`, {
              method: 'GET', credentials: 'same-origin', cache: 'no-store',
              headers: this.headers({ accept: 'text/event-stream', ...replay }), signal: wire.signal,
            })
            : await fetch(`${this.base}/api/chat`, {
              method: 'POST',
              credentials: 'same-origin',
              cache: 'no-store',
              headers: this.headers({ 'content-type': 'application/json', accept: 'text/event-stream', ...replay }),
              body,
              signal: wire.signal,
            });
        } catch (e) {
          if (wire.signal.aborted || signal.aborted) throw abortError();
          if (messageId !== null && isOffline()) { onConnection?.('offline'); dropped = true; await this.waitOnline(signal); continue; } // offline: wait, don't burn retries
          if (messageId !== null && retries < this.opts.maxRetries) { onConnection?.('reconnecting'); dropped = true; await this.backoff(++retries, signal); continue; }
          if (messageId !== null) throw e;
          // Never reached the server: say why, as a retryable error part (Retry = regenerate).
          yield { type: 'part', part: networkError() };
          yield { type: 'message.end', messageId: '', finishReason: 'error' };
          return;
        }
        noteServerDate(res.headers.get('date'));
        // §6.4: an expired/answered clarification is 409; send the reply as a fresh question instead.
        if (res.status === 409 && resumeFallback && messageId === null) {
          resumeFallback = false;
          body = JSON.stringify({ conversationId, messages: users, ...opt });
          continue;
        }
        // The server no longer has the answer to regenerate (ephemeral history, other branch): ask again.
        if (res.status === 404 && regenerateFallback && messageId === null) {
          regenerateFallback = false;
          body = JSON.stringify({ conversationId, messages: users, ...opt });
          continue;
        }
        // 429 before the answer starts: wait out Retry-After (status line counts down), then send the same request again.
        if (res.status === 429 && messageId === null && rateRetries < this.opts.maxRateRetries) {
          const wait = retryAfterOf(res) ?? this.opts.retryBaseMs * 2 ** rateRetries;
          if (wait <= this.opts.maxRateWaitMs) {
            rateRetries++;
            onConnection?.('rate_limited', { retryInMs: wait });
            dropped = true;
            await this.sleep(wait, signal);
            continue;
          }
        }
        if (viaGet && (res.status === 404 || res.status === 405) && (await errorCode(res)) !== 'stream_gone') {
          useGet = false; // no GET resume route (1.0 server): replay over POST right away
          continue;
        }
        if (!res.ok || !res.body) {
          if (messageId !== null && res.status >= 500 && retries < this.opts.maxRetries) { onConnection?.('reconnecting'); dropped = true; await this.backoff(++retries, signal); continue; }
          // Session expired while re-attaching to a running answer: keep what already streamed, end it with the
          // sign-in card (not "Connection lost"), and tell the session layer so the app can prompt for sign-in.
          if (messageId !== null && res.status === 401) {
            this.auth.onExpired();
            yield { type: 'part', part: await httpError(res) };
            yield { type: 'message.end', messageId, finishReason: 'error' };
            return;
          }
          // Rate-limited while re-attaching: wait out Retry-After (capped) like a 5xx, instead of dropping the answer.
          if (messageId !== null && res.status === 429 && retries < this.opts.maxRetries) {
            const wait = retryAfterOf(res);
            if (wait === null || wait <= this.opts.maxRateWaitMs) {
              onConnection?.('reconnecting');
              dropped = true;
              retries++;
              if (wait === null) await this.backoff(retries, signal); else await this.sleep(wait, signal);
              continue;
            }
          }
          if (messageId !== null && res.status === 404) throw streamGone(); // §8 stream_gone: useChat reloads the stored answer
          if (messageId !== null) throw new Error(`replay ${res.status}`); // the run is gone: useChat shows "Connection lost"
          if (res.status === 401) this.auth.onExpired();
          yield { type: 'part', part: await httpError(res) };
          yield { type: 'message.end', messageId: '', finishReason: 'error' };
          return;
        }
        try {
          for await (const ev of parseSse(guardBody(res.body, this.opts))) {
            if ((ev as { type: string }).type === 'heartbeat') continue;
            const seq = (ev as { seq?: number }).seq;
            if (typeof seq === 'number') {
              if (seq <= lastSeq) continue; // replay overlap
              lastSeq = seq;
            }
            if (ev.type === 'message.start') this.current = messageId = ev.messageId;
            retries = 0;
            if (dropped) { dropped = false; onConnection?.('live'); }
            yield ev;
            if (ev.type === 'message.end') return;
          }
        } catch (e) {
          if (wire.signal.aborted) throw abortError();
          if (e instanceof StreamGuardError && e.reason === 'too_large') {
            // Re-attaching would replay the same event: end the answer with what already arrived.
            yield { type: 'part', part: tooLargeError() };
            yield { type: 'message.end', messageId: messageId ?? '', finishReason: 'error' };
            return;
          }
          if (e instanceof StreamGuardError && messageId === null) {
            yield { type: 'part', part: stalledError() };
            yield { type: 'message.end', messageId: '', finishReason: 'error' };
            return;
          }
          if (messageId === null) throw e;
        }
        // The stream closed without message.end: a dropped connection.
        if (signal.aborted) throw abortError();
        if (isOffline()) { onConnection?.('offline'); dropped = true; await this.waitOnline(signal); continue; }
        if (messageId === null || retries >= this.opts.maxRetries) throw new Error('stream dropped');
        onConnection?.('reconnecting');
        dropped = true;
        await this.backoff(++retries, signal);
      }
    } finally {
      clearTimeout(graceTimer);
      signal.removeEventListener('abort', onAbort);
      this.current = null;
    }
  }

  /** Wait for the browser to come back online (bounded: the server replays for 60 s after a run ends). */
  private waitOnline(signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      const done = () => { clearTimeout(t); window.removeEventListener('online', done); signal.removeEventListener('abort', aborted); resolve(); };
      const aborted = () => { clearTimeout(t); window.removeEventListener('online', done); reject(abortError()); };
      const t = setTimeout(() => { window.removeEventListener('online', done); signal.removeEventListener('abort', aborted); reject(new Error('offline')); }, this.opts.offlineWaitMs);
      if (signal.aborted) return aborted();
      window.addEventListener('online', done, { once: true });
      signal.addEventListener('abort', aborted, { once: true });
    });
  }

  /** CONTRACT.md §8 reconnect schedule: base/2, 2×base, 4×base, 8×base (250 ms, 1 s, 2 s, 4 s at the default 500). */
  private backoff(attempt: number, signal: AbortSignal): Promise<void> {
    const ms = attempt === 1 ? this.opts.retryBaseMs / 2 : this.opts.retryBaseMs * 2 ** (attempt - 1);
    return this.sleep(Math.min(ms, 4000), signal);
  }

  private sleep(ms: number, signal: AbortSignal): Promise<void> {
    return new Promise((resolve, reject) => {
      if (signal.aborted) return reject(abortError());
      const t = setTimeout(resolve, ms);
      signal.addEventListener('abort', () => { clearTimeout(t); reject(abortError()); }, { once: true });
    });
  }

  /** PUT (or DELETE when `req` is null) the answer's feedback, contract 1.7.0 §13. */
  feedback(conversationId: string, messageId: string, req: FeedbackRequest | null): Promise<Feedback | null> {
    return sendFeedback(conversationId, messageId, req, { base: this.base, init: { credentials: 'same-origin', headers: this.headers() } });
  }
  /** GET /api/capabilities, signed in so `available` reflects the caller's organization (contract 1.5.0 scope). */
  capabilities(): Promise<Capabilities> {
    return loadCapabilities(this.base, (u, init) => fetch(u, { ...init, headers: this.headers((init?.headers ?? {}) as Record<string, string>) }));
  }

  /** GET /api/conversations/{id}/export (contract 1.8.0 §15): the server's own Markdown/JSON, named by Content-Disposition. */
  async exportConversation(id: string, format: ExportFormat): Promise<{ blob: Blob; filename: string }> {
    const res = await fetch(exportUrl(id, format, this.base), { credentials: 'same-origin', cache: 'no-store', headers: this.headers() });
    if (!res.ok) throw new Error(`export ${res.status}`);
    return { blob: await res.blob(), filename: serverExportFilename(res.headers.get('content-disposition'), format) };
  }

  /** Contract 1.11.0 §17: org-scoped, read-only share links. Auth (and the CSRF header on cookie sessions) come from the session. */
  private shareOpts() { return { base: this.base, init: { credentials: 'same-origin' as const, cache: 'no-store' as const, headers: this.headers() } }; }
  getShare(conversationId: string): Promise<ShareLink | null> { return getShare(conversationId, this.shareOpts()); }
  setShare(conversationId: string, includeData: boolean): Promise<ShareLink> { return setShare(conversationId, includeData, this.shareOpts()); }
  revokeShare(conversationId: string): Promise<boolean> { return revokeShare(conversationId, this.shareOpts()); }
  loadShared(shareId: string): Promise<SharedConversation | null> { return loadShared(shareId, this.shareOpts()); }

  async fetchBlob(id: string): Promise<Blob> {
    const res = await fetch(`${this.base}/api/download/${encodeURIComponent(id)}`, { credentials: 'same-origin', cache: 'no-store', headers: this.headers() });
    if (!res.ok) throw new Error(`blob ${res.status}`);
    return res.blob();
  }
}

/** Why `guardBody` gave up on a stream. */
export class StreamGuardError extends Error {
  constructor(readonly reason: 'stalled' | 'too_large') { super(`stream_${reason}`); this.name = 'StreamGuardError'; }
}

export function stalledError(): ErrorPart {
  return { type: 'error', id: 'err-stalled', code: 'timeout', title: 'The analyst stopped responding', message: 'No reply arrived for a while. Try again.', retryable: true };
}

export function tooLargeError(): ErrorPart {
  return { type: 'error', id: 'err-too-large', code: 'internal', title: 'Answer too large to show', message: 'Ask for fewer rows or a summary instead.', retryable: false };
}

/**
 * Wraps a response body with two limits: no bytes for `idleTimeoutMs` (the server heartbeats every 15 s) errors the
 * stream as stalled, and one line growing past `maxFrameBytes` without a line break errors it as too large. Either
 * way the underlying connection is cancelled, so a hung proxy or runaway event can't hold the tab.
 */
export function guardBody(body: ReadableStream<Uint8Array>, o: Pick<HttpAdapterOptions, 'idleTimeoutMs' | 'maxFrameBytes'>): ReadableStream<Uint8Array> {
  const reader = body.getReader();
  let sinceBreak = 0;
  return new ReadableStream<Uint8Array>({
    async pull(ctrl) {
      let timer: ReturnType<typeof setTimeout> | undefined;
      const idle = new Promise<never>((_, reject) => { timer = setTimeout(() => reject(new StreamGuardError('stalled')), o.idleTimeoutMs); });
      try {
        const r = await Promise.race([reader.read(), idle]);
        if (r.done) { ctrl.close(); return; }
        const c = r.value;
        const brk = Math.max(c.lastIndexOf(10), c.lastIndexOf(13));
        sinceBreak = brk === -1 ? sinceBreak + c.byteLength : c.byteLength - brk - 1;
        if (sinceBreak > o.maxFrameBytes) throw new StreamGuardError('too_large');
        ctrl.enqueue(c);
      } catch (e) {
        reader.cancel().catch(() => {});
        ctrl.error(e);
      } finally {
        clearTimeout(timer);
      }
    },
    cancel(reason) { return reader.cancel(reason); },
  });
}

/**
 * Fail-safe SSE events over a fetch body (contract 1.12.0 `readSse`). A malformed frame (non-JSON, or no string
 * `type`) is skipped and counted instead of throwing, so one bad frame can't kill a live answer. CRLF and lone CR
 * line endings are accepted, and frames split across chunks are reassembled. Frame content is never logged (PHI).
 */
export async function* parseSse(body: ReadableStream<Uint8Array>, decoder = new SseDecoder()): AsyncIterable<StreamEvent> {
  for await (const frame of readSse(body, decoder)) yield frame.event as unknown as StreamEvent;
}
