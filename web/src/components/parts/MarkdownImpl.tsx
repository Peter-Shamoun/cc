import { memo, useEffect, useState, type ReactNode } from 'react';
import { defaultRehypePlugins, Streamdown } from 'streamdown';
import ReactMarkdown from 'react-markdown';
import clsx from 'clsx';
import { CodeBlock } from './CodeBlock';
import { safeHref } from '../../render/safeHref';
import { currentMath, loadMath, mayContainMath, protectCurrency, repairStreamingTail, type MathPlugin } from './markdownText';
// Streamdown (Apache-2.0) word fade-in keyframes for [data-sd-animate]; plain stylesheet, CSP-safe.
import 'streamdown/styles.css';

/**
 * The markdown renderer chunk (QA-P8). Streamdown brings marked, micromark/mdast, rehype-raw
 * (parse5), tailwind-merge and react-markdown: ~1 MB of source the empty state never needs. It is
 * loaded through ./Markdown (lazy, preloaded at idle) and must not be imported from anywhere else.
 */

function useMath(protectedText: string): MathPlugin | null {
  const need = !currentMath() && mayContainMath(protectedText);
  const [plugin, setPlugin] = useState<MathPlugin | null>(currentMath);
  useEffect(() => {
    if (!need) return;
    let alive = true;
    loadMath().then((p) => { if (alive) setPlugin(p); }).catch(() => undefined);
    return () => { alive = false; };
  }, [need]);
  return plugin ?? currentMath();
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
export const MarkdownImpl = memo(function MarkdownImpl({ text, streaming }: { text: string; streaming?: boolean }) {
  const source = protectCurrency(streaming ? repairStreamingTail(text) : text);
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

/** Inline markdown only (bold, code, links) for callout and clarify text. */
export const InlineImpl = ({ text }: { text: string }) => (
  <ReactMarkdown allowedElements={['p', 'strong', 'em', 'code', 'a']} unwrapDisallowed components={{ p: ({ children }) => <>{children}</> }}>
    {text}
  </ReactMarkdown>
);
