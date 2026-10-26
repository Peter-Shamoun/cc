/**
 * Dev-only page (dev-torture.html, served by `vite` dev, never part of the build): renders PR #106's torture
 * corpus plus the long streaming docs at a stream position (`?frac=0.5`) in the real chat column, so
 * scripts/e2e_answer_torture.mjs can screenshot and measure every case in both themes. Synthetic data only.
 */
import ReactDOM from 'react-dom/client';
import '../index.css';
import { Markdown, preloadMarkdown, loadMath } from '../components/parts/Markdown';
import { DataTable } from '../components/parts/DataTable';
import { markdownFixtures } from '../render/__fixtures__/markdown';
import { streamingDocs } from '../render/__fixtures__/streaming';
import { tableFixtures } from '../render/__fixtures__/tables';
import type { TablePart } from '../lib/contract';

const q = new URLSearchParams(location.search);
const frac = Math.min(1, Math.max(0, Number(q.get('frac') ?? '1')));
const only = q.get('only');
const docs = [...markdownFixtures.map((f) => ({ id: f.id, markdown: f.markdown })), ...streamingDocs].filter((d) => !only || only.split(',').includes(d.id));

function cut(s: string) {
  if (frac >= 1) return s;
  // Cut on a code point boundary so emoji are never split into lone surrogates.
  const cps = Array.from(s);
  return cps.slice(0, Math.max(1, Math.round(cps.length * frac))).join('');
}

function Page() {
  return (
    <main className="mx-auto w-full max-w-[48rem] px-4 py-6 md:px-6">
      {docs.map((d) => (
        <section key={d.id} data-case={d.id} className="mb-6 border-b border-line pb-4">
          <div className="mb-2 font-mono text-[11px] text-fg-subtle">{d.id} · {frac < 1 ? `streaming ${Math.round(frac * 100)}%` : 'final'}</div>
          <Markdown text={cut(d.markdown)} streaming={frac < 1} />
        </section>
      ))}
      {!only && tableFixtures.filter((t) => t.id !== 'tall-10k').map((t) => (
        <section key={t.id} data-case={`table-${t.id}`} className="mb-6 border-b border-line pb-4">
          <div className="mb-2 font-mono text-[11px] text-fg-subtle">table {t.id}</div>
          <DataTable part={t.part as TablePart} />
        </section>
      ))}
    </main>
  );
}

void Promise.all([preloadMarkdown(), loadMath()]).then(() => {
  ReactDOM.createRoot(document.getElementById('root')!).render(<Page />);
  requestAnimationFrame(() => requestAnimationFrame(() => document.body.setAttribute('data-ready', '')));
});
