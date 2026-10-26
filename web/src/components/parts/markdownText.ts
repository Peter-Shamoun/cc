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
let mathLoading: Promise<MathPlugin> | null = null;
export function loadMath(): Promise<MathPlugin> {
  mathLoading ??= import('./mathPlugin').then((m) => (mathPlugin = m.mathPlugin));
  return mathLoading;
}
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
