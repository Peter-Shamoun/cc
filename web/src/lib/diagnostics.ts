/**
 * Copyable crash diagnostics. Built ONLY from allowlisted, non-PHI facts: the reference id, time, build, the error's
 * class name, bundle frames (file:line:col), the allowlisted context, the route *kind* (never an id), browser family
 * and major version (never the raw user agent), viewport, theme, online state and the recent error ids.
 *
 * The error message is deliberately left out. Even redacted, it is user-adjacent text, and this block gets pasted
 * into tickets and chats. The redacted message stays in the in-memory ErrorRecord for a backend reporter only.
 */
import { CONTRACT_VERSION } from '../api/contract';
import { recentErrors, type ErrorRecord } from './errorReporting';

const CHUNK_LOAD = /Failed to fetch dynamically imported module|error loading dynamically imported module|Importing a module script failed|ChunkLoadError|Loading (CSS )?chunk \S+ failed|Unable to preload CSS/i;

/** A lazy chunk failed to load (dropped connection or a deploy replaced the hashed files). Tested on the raw error, before redaction. */
export function isChunkLoadError(e: unknown): boolean {
  const name = (e as { name?: unknown } | null)?.name;
  const msg = (e as { message?: unknown } | null)?.message ?? e;
  return name === 'ChunkLoadError' || (typeof msg === 'string' && CHUNK_LOAD.test(msg));
}

export function browserFamily(ua: string): string {
  // Most specific first: Edge and Opera also claim Chrome; iOS Chrome/Firefox also claim Safari.
  const tokens: [string, string][] = [['Edg', 'Edge'], ['OPR', 'Opera'], ['FxiOS', 'Firefox'], ['CriOS', 'Chrome'], ['Firefox', 'Firefox'], ['HeadlessChrome', 'Chrome (headless)'], ['Chrome', 'Chrome']];
  let m: [string, string] | null = null;
  for (const [t, label] of tokens) {
    const v = new RegExp(`\\b${t}/(\\d+)`).exec(ua);
    if (v) { m = [label, v[1]]; break; }
  }
  const os = /Windows/.test(ua) ? 'Windows' : /iPhone|iPad|iPod/.test(ua) ? 'iOS' : /Android/.test(ua) ? 'Android' : /CrOS/.test(ua) ? 'ChromeOS' : /Mac OS X|Macintosh/.test(ua) ? 'macOS' : /Linux/.test(ua) ? 'Linux' : 'other';
  if (m) return `${m[0]} ${m[1]} · ${os}`;
  const s = /Version\/(\d+)[\d.]* .*Safari\//.exec(ua);
  return s ? `Safari ${s[1]} · ${os}` : `other · ${os}`;
}

/** The route kind only. Conversation ids are opaque, but a support paste doesn't need them. */
export function routeKind(hash: string, search: string): string {
  const kind = /^#\/c\/[\w-]+$/.test(hash) ? 'conversation' : hash === '' || hash === '#' || hash === '#/' ? 'new chat' : 'other';
  return /[?&]share=|[?&]shared=/.test(search) ? `${kind} (shared)` : kind;
}

export function buildDiagnostics(rec: ErrorRecord, env: { now?: Date; win?: Window } = {}): string {
  const win = env.win ?? window;
  const doc = win.document.documentElement;
  const ctx = Object.entries(rec.context).map(([k, v]) => `${k}=${v}`).join(', ') || 'none';
  const others = recentErrors().filter((r) => r.id !== rec.id).slice(-5).map((r) => `${r.id} ${r.name} (${r.kind})`);
  return [
    'ClaimClarity diagnostics (no message text or patient data)',
    `Reference: ${rec.id}`,
    `Time: ${new Date(rec.at).toISOString()}`,
    `Build: contract ${CONTRACT_VERSION}, ${import.meta.env.MODE}`,
    `Error: ${rec.name} (${rec.kind})`,
    `Frames: ${rec.frames.length ? rec.frames.slice(0, 6).join(' | ') : 'none'}`,
    `Context: ${ctx}`,
    `Route: ${routeKind(win.location.hash, win.location.search)}`,
    `Browser: ${browserFamily(win.navigator.userAgent)}`,
    `Viewport: ${win.innerWidth}x${win.innerHeight} @${win.devicePixelRatio || 1}x`,
    `Theme: ${doc.classList.contains('dark') ? 'dark' : 'light'} / ${doc.dataset.palette ?? 'neutral'}`,
    `Online: ${win.navigator.onLine ? 'yes' : 'no'}`,
    `Other recent errors: ${others.length ? others.join('; ') : 'none'}`,
  ].join('\n');
}
