import { memo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import clsx from 'clsx';
import { CodeBlock } from './CodeBlock';

export const Markdown = memo(function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  return (
    <div className={clsx('prose-chat', streaming && 'streaming-caret')}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
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
            const text = String(children ?? '');
            if (m || text.includes('\n')) return <CodeBlock code={text.replace(/\n$/, '')} language={m?.[1] ?? 'text'} />;
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
      </ReactMarkdown>
    </div>
  );
});
