import { memo, useEffect, useState } from 'react';
import clsx from 'clsx';

export { displayMath, loadMath, mayContainMath } from './markdownText';

/**
 * Lazy front for the markdown renderer (QA-P8: slow-3G fresh load was blank ~10 s because
 * Streamdown, marked, micromark, rehype-raw/parse5, tailwind-merge and react-markdown sat in the
 * entry chunk). The empty state never renders markdown, so the stack loads as its own chunk:
 * main.tsx preloads it at idle after first paint, and once it is in memory every render is
 * synchronous (no Suspense, no flash). Until then an answer shows as plain wrapped text in the
 * same container, which is what a reader sees for one frame at most on a normal connection.
 */
type Impl = typeof import('./MarkdownImpl');
let impl: Impl | null = null;
let loading: Promise<Impl> | null = null;
export function preloadMarkdown(): Promise<Impl> {
  loading ??= import('./MarkdownImpl').then((m) => (impl = m));
  return loading;
}

function useImpl(): Impl | null {
  const [m, setM] = useState<Impl | null>(impl);
  useEffect(() => {
    if (m) return;
    let alive = true;
    preloadMarkdown().then((x) => { if (alive) setM(x); }).catch(() => undefined);
    return () => { alive = false; };
  }, [m]);
  return m ?? impl;
}

const BOX = { overflowWrap: 'anywhere', wordBreak: 'break-word', maxWidth: '100%', minWidth: 0, unicodeBidi: 'isolate' } as const;

/** Assistant prose. See MarkdownImpl for the renderer and its safety rules. */
export const Markdown = memo(function Markdown({ text, streaming }: { text: string; streaming?: boolean }) {
  const m = useImpl();
  if (m) return <m.MarkdownImpl text={text} streaming={streaming} />;
  return (
    <div className={clsx('prose-chat', streaming && 'streaming-caret')} data-testid="markdown" data-pending="" dir="auto" style={BOX}>
      <p className="whitespace-pre-wrap">{text}</p>
    </div>
  );
});

/** Inline markdown only (bold, code, links) for callout and clarify text; plain text until loaded. */
export function Inline({ text }: { text: string }) {
  const m = useImpl();
  return m ? <m.InlineImpl text={text} /> : <>{text}</>;
}
