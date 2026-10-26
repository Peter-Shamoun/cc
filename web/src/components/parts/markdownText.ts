import { onceOk } from '../../lib/onceOk';
/**
 * Markdown text helpers with no renderer dependency: kept out of the lazy Streamdown chunk so the
 * entry bundle (and tests) can use them without pulling in the markdown stack (QA-P8).
 */
/**
 * `$…$` and `$$…$$` (spec §4.1). Currency such as `$412` is escaped before parse
 * so it stays text. KaTeX errors use the app danger color. The plugin (KaTeX, its CSS and fonts)
 * is loaded on first use, so answers without math never download it.
 */
export type MathPlugin = typeof import('./mathPlugin').mathPlugin;
let mathPlugin: MathPlugin | null = null;
/** The loaded plugin, or null before the first `loadMath()` resolves. */
export const currentMath = (): MathPlugin | null => mathPlugin;
// A failed load is retried on next use (lib/onceOk), not cached.
export const loadMath: () => Promise<MathPlugin> = onceOk(() => import('./mathPlugin').then((m) => (mathPlugin = m.mathPlugin)));
/** Unescaped `$` (after protectCurrency) or `\(`/`\[` delimiters mean the text may contain math. */
export function mayContainMath(protectedText: string): boolean {
  return /(^|[^\\])\$|\\\(|\\\[/.test(protectedText);
}
const CODE_SPAN = /```[\s\S]*?```|`[^`\n]+`|```[\s\S]*$/g;

/**
 * `$12` and `$1,234.56` are currency in this product, same as ChatGPT and Claude.
 * A dollar glued to a digit is escaped so it cannot open or close math. `$E = mc^2$`
 * is unchanged. Fenced and inline code are left alone, including a fence that is
 * still open while the message streams.
 */
/**
 * `$$x$$` written on a line of its own is a display equation (QA-S9). remark-math only treats `$$` as a
 * block when the fences sit on their own lines, so a one-line `$$…$$` is split onto three lines.
 * `$$x$$` inside a sentence stays inline.
 */
const ONE_LINE_DISPLAY = /^([ \t]*)\$\$(?!\$)([^\n]*?[^\\\n])\$\$[ \t]*$/gm;
export function displayMath(prose: string): string {
  return prose.replace(ONE_LINE_DISPLAY, (_m, indent: string, body: string) => `${indent}$$\n${indent}${body.trim()}\n${indent}$$`);
}

export function protectCurrency(markdown: string): string {
  const escape = (prose: string) => displayMath(prose).replace(/(^|[^\\])\$(?=\d)/g, '$1\\$');
  let out = '';
  let last = 0;
  for (const match of markdown.matchAll(CODE_SPAN)) {
    const start = match.index ?? 0;
    out += escape(markdown.slice(last, start));
    out += match[0];
    last = start + match[0].length;
  }
  return out + escape(markdown.slice(last));
}

const TAIL_MARKER = /(^|[\s([{>])(\*{1,3}|_{1,3}|~{1,2})$/;
const RULE_CELL = /^\s*:?-{3,}:?\s*$/;
const PARTIAL_RULE = /^\s*\|?(\s*:?-*:?\s*\|?)*\s*$/;
const splitCells = (line: string) => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|');

/**
 * Streaming-only repair for the last line of a partial answer (the torture corpus, PR #106), applied before
 * Streamdown's own incomplete-markdown pass:
 * - An emphasis opener with nothing after it yet (`rose **`) would show as raw `**` for a frame: drop it.
 * - A table whose delimiter row is half-typed (`| Payer | Rate |\n| --`) would flash as a paragraph with raw
 *   pipes and dashes: complete the delimiter row from the header's cell count, keeping typed alignments.
 * Text inside an open code fence is left alone.
 */
function openDisplayMath(markdown: string): number {
  let at = -1;
  let pos = 0;
  for (const line of markdown.split('\n')) {
    const t = line.trim();
    if (t.startsWith('$$')) {
      const oneLine = t.length > 4 && t.endsWith('$$');
      if (at < 0 && !oneLine) at = pos;
      else if (at >= 0 && (t === '$$' || t.endsWith('$$'))) at = -1;
    } else if (at >= 0 && t.endsWith('$$')) at = -1;
    pos += line.length + 1;
  }
  if (at < 0 || markdown.slice(at).includes('\n\n')) return -1;
  return at;
}

/** How many balanced `{…}` groups follow `at` before anything else (whitespace aside), counting to the end. */
function groupsAfter(tex: string, at: number): number {
  let n = 0;
  let i = at;
  for (;;) {
    while (i < tex.length && /\s/.test(tex[i])) i++;
    if (tex[i] !== '{') return i >= tex.length ? n : 99;
    let depth = 0;
    for (; i < tex.length; i++) {
      if (tex[i] === '\\') { i++; continue; }
      if (tex[i] === '{') depth++;
      else if (tex[i] === '}' && --depth === 0) break;
    }
    if (depth !== 0) return n;
    n++;
    i++;
  }
}

/** The longest prefix of streaming TeX that KaTeX can parse: balanced groups, no cut command or script. */
export function completeTex(tex: string): string {
  if ((tex.match(/\\begin\{/g)?.length ?? 0) > (tex.match(/\\end\{/g)?.length ?? 0)) return '';
  let depth = 0;
  let safe = 0;
  for (let i = 0; i < tex.length; i++) {
    const c = tex[i];
    if (c === '\\') { i++; continue; }
    if (c === '{') depth++;
    else if (c === '}') depth = Math.max(0, depth - 1);
    if (depth === 0 && (c === '}' || /\s/.test(c))) safe = i + 1;
  }
  let out = depth === 0 ? tex : tex.slice(0, safe);
  // `\left(` needs its `\right)`: cut before the last unmatched one.
  while ((out.match(/\\left(?![a-zA-Z])/g)?.length ?? 0) > (out.match(/\\right(?![a-zA-Z])/g)?.length ?? 0)) out = out.slice(0, out.lastIndexOf('\\left'));
  if (/\\[a-zA-Z]*$/.test(out)) out = out.replace(/\\[a-zA-Z]*$/, '');
  // A dangling operator that needs an argument (`x^`, `\frac`-less `_`, alignment `&`, line break `\\`).
  out = out.replace(/(\s|\^|_|&|\\\\)+$/, (m) => (/[\^_&\\]/.test(m) ? '' : m));
  // Commands still waiting for their arguments (`\frac{n}` without `{d}`, `\sqrt` without a group) at the end.
  for (let changed = true; changed;) {
    changed = false;
    const re = /\\(d?frac|tfrac|binom|sqrt|text|mathrm|mathbf|operatorname|overline|underline|hat|bar|vec)(?![a-zA-Z])/g;
    let m: RegExpExecArray | null;
    let cut = -1;
    while ((m = re.exec(out))) {
      const need = /frac|binom/.test(m[1]) ? 2 : 1;
      if (groupsAfter(out, m.index + m[0].length) < need) { cut = m.index; break; }
    }
    if (cut >= 0) { out = out.slice(0, cut); changed = true; }
  }
  return out;
}

export function repairStreamingTail(markdown: string): string {
  const fences = markdown.match(/^[ \t]*(```|~~~)/gm)?.length ?? 0;
  if (fences % 2 === 1) return markdown;
  // A display-math block still being typed (`$$` opened, not yet closed) renders as a red KaTeX parse error
  // whenever the tail cuts a group (`\frac{a}{b`), a command (`\fra`) or a script (`x^`). Trim the TeX back to
  // its last complete point; hold the whole block back when nothing complete is left. Only at the tail: a
  // block the model left open followed by more paragraphs renders as written.
  const open = openDisplayMath(markdown);
  if (open >= 0) {
    const tex = completeTex(markdown.slice(open).replace(/^\s*\$\$/, ''));
    return tex.trim() ? `${markdown.slice(0, open)}$$${tex}` : markdown.slice(0, open);
  }
  const nl = markdown.lastIndexOf('\n');
  const last = markdown.slice(nl + 1);
  let head = markdown.slice(0, nl + 1);
  const m = TAIL_MARKER.exec(last);
  if (m) return head + last.slice(0, last.length - m[2].length);
  if (nl < 0 || !last.trim() || !PARTIAL_RULE.test(last) || !/[-|]/.test(last)) return markdown;
  const prevNl = markdown.lastIndexOf('\n', nl - 1);
  const header = markdown.slice(prevNl + 1, nl);
  const beforeHeader = prevNl < 0 ? '' : markdown.slice(markdown.lastIndexOf('\n', prevNl - 1) + 1, prevNl);
  if (!header.includes('|') || beforeHeader.includes('|')) return markdown;
  const n = splitCells(header).length;
  const typed = splitCells(last);
  const cells = Array.from({ length: n }, (_, i) => (typed[i] && RULE_CELL.test(typed[i]) ? typed[i].trim() : '---'));
  head = markdown.slice(0, nl + 1);
  return `${head}| ${cells.join(' | ')} |\n`;
}
