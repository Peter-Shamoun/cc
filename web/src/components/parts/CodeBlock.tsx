import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Database, Download, Maximize2 } from 'lucide-react';
import type { CodePart } from '../../lib/contract';
import { CopyButton, IconButton } from '../ui';
import { downloadText } from '../../lib/csv';
import { useTheme } from '../../lib/theme';
import { shikiHtml, type SafeHtml } from '../../security/trustedTypes';

/**
 * Fine-grained Shiki: only the five languages and four themes we render, and the JavaScript regex engine
 * instead of the oniguruma wasm. This keeps ~300 grammar chunks and a 225 KB (gz) wasm out of dist, and the
 * CSP no longer needs 'wasm-unsafe-eval'.
 */
type Highlighter = { codeToHtml: (code: string, opts: { lang: string; themes: Record<string, string>; defaultColor: string }) => string };
let hl: Promise<Highlighter> | null = null;
function highlighter() {
  hl ??= Promise.all([
    import('shiki/core'),
    import('shiki/engine/javascript'),
    Promise.all([import('shiki/langs/sql.mjs'), import('shiki/langs/python.mjs'), import('shiki/langs/json.mjs'), import('shiki/langs/typescript.mjs'), import('shiki/langs/bash.mjs')]),
    Promise.all([import('shiki/themes/github-light.mjs'), import('shiki/themes/github-dark.mjs'), import('shiki/themes/vitesse-light.mjs'), import('shiki/themes/vitesse-dark.mjs')]),
  ]).then(([core, engine, langs, themes]) =>
    core.createHighlighterCore({
      engine: engine.createJavaScriptRegexEngine(),
      langs: langs.map((m) => m.default),
      themes: themes.map((m) => m.default),
    }) as Promise<Highlighter>,
  );
  return hl;
}
const KNOWN = new Set(['sql', 'python', 'json', 'typescript', 'bash']);
const EXT: Record<string, string> = { sql: 'sql', python: 'py', json: 'json', typescript: 'ts', bash: 'sh' };

/** ChatGPT-style code block (spec §1.2b): one rounded container, small label top-left, copy top-right. */
export function CodeBlock({ code, language, title, onOpen, tall }: { code: string; language: string; title?: string; onOpen?: () => void; tall?: boolean }) {
  const [html, setHtml] = useState<SafeHtml | null>(null);
  const { palette } = useTheme();
  useEffect(() => {
    let alive = true;
    if (!KNOWN.has(language)) return;
    const t = setTimeout(() => {
      const [light, dark] = palette === 'paper' ? ['vitesse-light', 'vitesse-dark'] : ['github-light', 'github-dark'];
      highlighter()
        .then((h) => h.codeToHtml(code, { lang: language, themes: { light, dark }, defaultColor: 'light' }))
        .then((out) => alive && setHtml(shikiHtml(out)))
        .catch(() => {});
    }, 100);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [code, language, palette]);

  return (
    <div className="group/code relative my-4 overflow-hidden rounded-xl bg-[var(--code-bg)]" data-testid="code-block">
      <div className="flex items-center justify-between px-4 pt-2 text-xs text-fg-subtle">
        <span>{title ? `${language} · ${title}` : language}</span>
        <div className="flex items-center gap-0.5">
          <IconButton label="Download" className="h-7 w-7 opacity-0 group-hover/code:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100" onClick={() => downloadText(title ?? `snippet.${EXT[language] ?? 'txt'}`, code, 'text/plain')}>
            <Download size={14} />
          </IconButton>
          {onOpen && (
            <IconButton label="Open in panel" onClick={onOpen} className="h-7 w-7 opacity-0 group-hover/code:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100">
              <Maximize2 size={14} />
            </IconButton>
          )}
          <CopyButton text={code} label="Copy code" className="h-7 w-7" />
        </div>
      </div>
      <div tabIndex={0} aria-label={`${language} code`} className={tall ? 'shiki-wrap overflow-auto' : 'shiki-wrap max-h-[480px] overflow-auto'}>
        {html ? <div dangerouslySetInnerHTML={{ __html: html }} /> : <pre className="font-mono"><code>{code}</code></pre>}
      </div>
    </div>
  );
}

/** role=query parts render collapsed under "SQL ▸ 1 query" (spec §4.6). */
export function CodePartView({ part, onOpen }: { part: CodePart; onOpen?: () => void }) {
  const collapsible = part.role === 'query' && part.collapsed !== false;
  const [open, setOpen] = useState(!collapsible);
  if (!collapsible) return <CodeBlock code={part.code} language={part.language} title={part.title} onOpen={onOpen} />;
  return (
    <div className="my-2" data-testid="sql-disclosure">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1.5 rounded-md py-0.5 text-sm text-fg-muted hover:text-fg">
        <Database size={14} /> SQL {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} <span className="text-fg-subtle">1 query</span>
      </button>
      {open && <CodeBlock code={part.code} language={part.language} title={part.title} onOpen={onOpen} />}
    </div>
  );
}
