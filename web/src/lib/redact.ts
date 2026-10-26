/**
 * PHI-safe text for error reports. It mirrors api/redact.py, and the shared vectors are in redact.vectors.json.
 *
 * This is a heuristic last line of defence, not de-identification. The rule is still never to put user text, SQL
 * or row values in an error report. Quoted strings go first because runtime errors quote their input
 * (`Unexpected token 'J', "Jane…" is not valid JSON`).
 */
const RULES: [RegExp, string][] = [
  [/"[^"]*"|'[^']*'|\u2018[^\u2019]*\u2019|\u201c[^\u201d]*\u201d|`[^`]*`/g, '[text]'],
  [/[\w.+-]+@[\w-]+(?:\.[\w-]+)+/g, '[email]'],
  [/\b\d{3}-\d{2}-\d{4}\b/g, '[ssn]'],
  [/(?:\+?1[ .-]?)?(?:\(\d{3}\)\s?|\b\d{3}[ .-])\d{3}[ .-]\d{4}\b/g, '[phone]'],
  [/\b(?:\d{1,2}\/\d{1,2}\/\d{2,4}|(?:19|20)\d{2}-\d{2}-\d{2})\b/g, '[date]'],
  [/\b(?=[A-Za-z0-9]*\d)(?=[A-Za-z0-9]*[A-Za-z])[A-Za-z0-9]{8,}\b/g, '[id]'],
  [/\b\d{6,}\b/g, '[number]'],
];
export const MAX_LEN = 300;

export function redact(text: unknown, maxLen = MAX_LEN): string {
  let s = String(text);
  for (const [rx, repl] of RULES) s = s.replace(rx, repl);
  return s.length <= maxLen ? s : `${s.slice(0, maxLen)}\u2026`;
}

export interface SafeError {
  name: string;
  message: string;
  /** `file:line:col` only: bundle basenames, no function text, no URLs with query strings. */
  frames: string[];
}

const NAME = /^[A-Za-z][A-Za-z0-9]{0,39}$/;
const FRAME = /([\w.-]+\.(?:m?js|tsx?|jsx))(?:\?[^:\s)]*)?:(\d+):(\d+)/g;

/** Reduce anything thrown to a PHI-safe record. Never stringifies objects (they could be user data). */
export function safeError(e: unknown): SafeError {
  if (e instanceof Error) {
    const frames = [...(e.stack ?? '').matchAll(FRAME)].slice(0, 10).map((m) => `${m[1]}:${m[2]}:${m[3]}`);
    return { name: NAME.test(e.name) ? e.name : 'Error', message: redact(e.message), frames };
  }
  if (typeof e === 'string') return { name: 'Error', message: redact(e), frames: [] };
  return { name: 'NonError', message: `thrown ${e === null ? 'null' : typeof e}`, frames: [] };
}
