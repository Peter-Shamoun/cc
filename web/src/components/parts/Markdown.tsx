import { memo } from 'react';
import { Streamdown } from 'streamdown';
import clsx from 'clsx';
import { CodeBlock } from './CodeBlock';

/**
 * Assistant prose renderer. Streamdown (Vercel, Apache-2.0, see web/THIRD_PARTY_NOTICES.md)
 * repairs unfinished markdown while tokens stream in (an open `**`, a half-written table or
 * code fence renders as if closed) and memoizes per block so only the tail re-renders.
 * Its default rehype-harden / sanitize pipeline stays on; we only swap a few components.
 */
export const Markdown = memo(function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className={clsx('prose-chat', streaming && 'streaming-caret')} data-testid="markdown">
      <Streamdown
        mode={streaming ? 'streaming' : 'static'}
        isAnimating={!!streaming}
        parseIncompleteMarkdown
        controls={false}
        components={{
          // No remote images in markdown (spec §4.1, §6.11): images arrive only as image parts.
          img: ({ alt }) => <span className="text-fg-subtle">[image: {alt}]</span>,
          a: ({ href, children }) => (
            <a href={href} target="_blank" rel="noopener noreferrer nofollow">
              {children}
            </a>
          ),
          code({ className, children }) {
            const m = /language-(\w+)/.exec(className ?? '');
            const t = String(children ?? '');
            if (m || t.includes('\n')) return <CodeBlock code={t.replace(/\n$/, '')} language={m?.[1] ?? 'text'} />;
            return <code>{children}</code>;
          },
          pre: ({ children }) => <>{children}</>,
          table: ({ children }) => (
            <div className="overflow-x-auto">
              <table>{children}</table>
            </div>
          ),
        }}
      >
        {text}
      </Streamdown>
    </div>
  );
});
