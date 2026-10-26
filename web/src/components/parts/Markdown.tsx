import { memo, useEffect, useState, type ReactNode } from 'react';
import { defaultRehypePlugins, Streamdown } from 'streamdown';
import clsx from 'clsx';
import { CodeBlock } from './CodeBlock';
import { safeHref } from '../../render/safeHref';
// Streamdown (Apache-2.0) word fade-in keyframes for [data-sd-animate]; plain stylesheet, CSP-safe.
import 'streamdown/styles.css';

/**
 * `$…$` and `$$…$$` (spec §4.1). Currency such as `$412` is escaped before parse
 * so it stays text. KaTeX errors use the app danger color. The plugin (KaTeX, its CSS and fonts)
 * is loaded on first use, so answers without math never download it.
 */
type MathPlugin = typeof import('./mathPlugin').mathPlugin;
let mathPlugin: MathPlugin | null = null;
let mathLoading: Promise<MathPlugin> | null = null;
export function loadMath(): Promise<MathPlugin> {
  mathLoading ??= import('./mathPlugin').then((m) => (mathPlugin = m.mathPlugin));
  return mathLoading;
}
/** Unescaped `$` (after protectCurrency) or `\(`/`\[` delimiters mean the text may contain math. */
export function mayContainMath(protectedText: string): boolean {
  return /(^|[^\\])\$|\\\(|\\\[/.test(protectedText);
}
function useMath(protectedText: string): MathPlugin | null {
  const need = !mathPlugin && mayContainMath(protectedText);
  const [plugin, setPlugin] = useState<MathPlugin | null>(mathPlugin);
  useEffect(() => {
    if (!need) return;
    let alive = true;
    loadMath().then((p) => { if (alive) setPlugin(p); }).catch(() => undefined);
    return () => { alive = false; };
  }, [need]);
  return plugin ?? mathPlugin;
}

/**
 * Raw HTML is omitted on purpose (spec §4.1, §6.11). ChatGPT and Claude show
 * the tags as text; they do not build elements, so a `<script>` or an
 * `onerror` handler never becomes a node. Sanitize + harden still run for
 * markdown links and images. The math plugin is appended by Streamdown.
 */
const rehypePlugins = [defaultRehypePlugins.sanitize, defaultRehypePlugins.harden];

/**
 * New words fade in as they arrive (ChatGPT/Claude both soften the token edge rather than
 * popping whole chunks). Only while streaming; prefers-reduced-motion zeroes it in index.css.
 */
const STREAM_ANIMATION = { animation: 'fadeIn', duration: 160, easing: 'ease-out', sep: 'word', stagger: 8, maxBacklogMs: 160 } as const;

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

function protectCurrency(markdown: string): string {
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

function textContent(node: ReactNode): string {
  if (node == null || typeof node === 'boolean') return '';
  if (typeof node === 'string' || typeof node === 'number') return String(node);
  if (Array.isArray(node)) return node.map(textContent).join('');
  return '';
}

/**
 * Assistant prose renderer. Streamdown repairs unfinished markdown while tokens
 * stream in (an open `**`, a half-written table or code fence renders as if
 * closed) and memoizes per block so only the tail re-renders.
 */
export const Markdown = memo(function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  const source = protectCurrency(text);
  const math = useMath(source);
  return (
    <div
      className={clsx('prose-chat', streaming && 'streaming-caret')}
      data-testid="markdown"
      style={{ overflowWrap: 'anywhere', wordBreak: 'break-word', maxWidth: '100%', minWidth: 0, unicodeBidi: 'isolate' }}
    >
      <Streamdown
        mode={streaming ? 'streaming' : 'static'}
        dir="auto"
        isAnimating={!!streaming}
        animated={streaming ? STREAM_ANIMATION : false}
        parseIncompleteMarkdown
        controls={false}
        plugins={math ? { math } : undefined}
        rehypePlugins={rehypePlugins}
        components={{
          // No remote images in markdown (spec §4.1, §6.11): images arrive only as image parts.
          img: ({ alt }) => <span className="text-fg-subtle">[image: {alt}]</span>,
          a: ({ href, children }) => {
            const safe = safeHref(href);
            if (!safe) return <span>{children}</span>;
            return (
              <a href={safe} target="_blank" rel="noopener noreferrer nofollow">
                {children}
              </a>
            );
          },
          // Task lists are read-only (spec §4.1). Do not spread unknown props onto the input.
          input: ({ type, checked }) =>
            type === 'checkbox' ? <input type="checkbox" checked={!!checked} disabled readOnly /> : null,
          code({ className, children }) {
            const languageClass = typeof className === 'string' ? className : '';
            const m = /language-(\w+)/.exec(languageClass);
            const t = textContent(children);
            if (m || t.includes('\n')) return <CodeBlock code={t.replace(/\n$/, '')} language={m?.[1] ?? 'text'} />;
            return <code>{children}</code>;
          },
          pre: ({ children }) => <>{children}</>,
          table: ({ children }) => (
            <div className="overflow-x-auto max-w-full">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {source}
      </Streamdown>
    </div>
  );
});
