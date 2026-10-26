import { useEffect, useState } from 'react';
import { ChevronDown, ChevronRight, Database, Download, EyeOff, Maximize2, WrapText } from 'lucide-react';
import type { CodePart } from '../../lib/contract';
import { CopyButton, IconButton } from '../ui';
import { downloadText } from '../../lib/csv';
import { maskComment } from '../../lib/feedback';
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

/**
 * Contract 1.14.0 §10: the shown SQL has patient-bearing literals replaced by `?`. Say so, quietly, next to the
 * SQL; the executed SQL is unchanged, and the shown text is for reading, never offered as runnable.
 */
export function MaskedNote({ count }: { count: number }) {
  return (
    <span className="inline-flex items-center gap-1 text-fg-subtle" data-testid="sql-masked"
      title={`${count} ${count === 1 ? 'value' : 'values'} that could identify a patient ${count === 1 ? 'is' : 'are'} shown as ?. The query ran with the real values; this copy is for reading, not for running.`}>
      <EyeOff size={12} aria-hidden /> Patient values hidden
    </span>
  );
}

/**
 * Code block (spec §1.2b), as observed in ChatGPT/Claude: one quiet rounded container, the language small at top-left,
 * a labelled "Copy code" at top-right, secondary actions (wrap, download, open) on hover. Long lines scroll inside the
 * block (never the page); Wrap lines soft-wraps them instead.
 */
export function CodeBlock({ code, language, title, onOpen, tall, masked }: { code: string; language: string; title?: string; onOpen?: () => void; tall?: boolean; masked?: number }) {
  const [html, setHtml] = useState<SafeHtml | null>(null);
  const [wrap, setWrap] = useState(false);
  const { palette } = useTheme();
  const label = language && language !== 'text' ? language : '';
  const copyable = masked && masked > 0 ? `${maskComment(masked)}\n${code}` : code;
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
        <span className="flex min-w-0 items-center gap-2"><span className="truncate" data-testid="code-lang">{[label, title].filter(Boolean).join(' · ')}</span>{!!masked && masked > 0 && <MaskedNote count={masked} />}</span>
        <div className="flex items-center gap-0.5">
          <IconButton label={wrap ? 'Don\'t wrap lines' : 'Wrap lines'} aria-pressed={wrap} className="h-7 w-7 opacity-0 group-hover/code:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100" onClick={() => setWrap((w) => !w)}>
            <WrapText size={14} />
          </IconButton>
          <IconButton label="Download" className="h-7 w-7 opacity-0 group-hover/code:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100" onClick={() => downloadText(title ?? `snippet.${EXT[language] ?? 'txt'}`, copyable, 'text/plain')}>
            <Download size={14} />
          </IconButton>
          {onOpen && (
            <IconButton label="Open in panel" onClick={onOpen} className="h-7 w-7 opacity-0 group-hover/code:opacity-100 focus:opacity-100 [@media(hover:none)]:opacity-100">
              <Maximize2 size={14} />
            </IconButton>
          )}
          {/* ChatGPT hands-on: icon-only copy that turns into a check; the name says "Copied" for screen readers. */}
          <CopyButton text={copyable} label="Copy code" className="h-7 w-7" />
        </div>
      </div>
      <div tabIndex={0} aria-label={`${label || 'Plain'} code`} data-wrap={wrap || undefined} className={tall ? 'shiki-wrap overflow-auto' : 'shiki-wrap max-h-[480px] overflow-auto'}>
        {html ? <div dangerouslySetInnerHTML={{ __html: html }} /> : <pre className="font-mono"><code>{code}</code></pre>}
      </div>
    </div>
  );
}

/** role=query parts render collapsed under "SQL ▸ 1 query" (spec §4.6). */
export function CodePartView({ part, onOpen }: { part: CodePart; onOpen?: () => void }) {
  const collapsible = part.role === 'query' && part.collapsed !== false;
  const [open, setOpen] = useState(!collapsible);
  const masked = (part as { maskedLiterals?: number }).maskedLiterals;
  if (!collapsible) return <CodeBlock code={part.code} language={part.language} title={part.title} onOpen={onOpen} masked={masked} />;
  return (
    <div className="my-2" data-testid="sql-disclosure">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)} className="inline-flex items-center gap-1.5 rounded-md py-0.5 text-sm text-fg-muted hover:text-fg">
        <Database size={14} /> SQL {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />} <span className="text-fg-subtle">1 query</span>
      </button>
      {open && <CodeBlock code={part.code} language={part.language} title={part.title} onOpen={onOpen} masked={masked} />}
    </div>
  );
}
