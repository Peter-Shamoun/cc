import { useEffect, useRef, useState } from 'react';
import { Download, Maximize2, FileJson } from 'lucide-react';
import { loader as createVegaLoader } from 'vega';
import type { ChartPart } from '../../lib/contract';
import { IconButton } from '../ui';
import { CHART_COLORS, useTheme } from '../../lib/theme';
import { csvFilename, downloadText, recordsToCsv } from '../../lib/csv';
import { buildSpec, chartRenderer } from '../../render/chartSpec';

export { buildSpec };

type View = { finalize: () => void; toImageURL: (t: 'png' | 'svg', scale?: number) => Promise<string> };

/** Refuse http(s) and file loads. Inline `values` never need the loader (spec §4.5, §6.11). */
function remoteBlockedLoader() {
  const loader = createVegaLoader();
  const reject = () => Promise.reject(new Error('Remote chart data is blocked'));
  loader.http = reject;
  loader.file = reject;
  return loader;
}

export function ChartView({ part, onOpen, full, onShowSpec }: { part: ChartPart; onOpen?: () => void; full?: boolean; onShowSpec?: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const viewRef = useRef<View | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { mode, palette } = useTheme();
  useEffect(() => {
    let alive = true;
    // ThemeProvider's effect runs after this one. Set the tokens first so the
    // first embed already matches the active mode and palette.
    const root = document.documentElement;
    root.classList.toggle('dark', mode === 'dark');
    root.dataset.palette = palette;
    import('vega-embed')
      .then(({ default: embed }) => {
        if (!alive || !ref.current) return;
        const dark = mode === 'dark';
        const css = getComputedStyle(root);
        const v = (n: string) => css.getPropertyValue(n).trim();
        const colors = CHART_COLORS[palette];
        return embed(ref.current, buildSpec(part) as never, {
          actions: false,
          renderer: chartRenderer(part),
          // vega-interpreter: no Function/eval on model-authored expressions (spec §6.9).
          ast: true,
          loader: remoteBlockedLoader(),
          tooltip: { theme: dark ? 'dark' : 'light' },
          config: {
            background: 'transparent',
            font: v('--font-sans') || 'sans-serif',
            axis: { labelColor: v('--fg-muted'), titleColor: v('--fg'), gridColor: dark ? '#3a3a3a' : '#ececec', domainColor: v('--border-strong'), tickColor: v('--border-strong'), labelFontSize: 12, titleFontSize: 12 },
            legend: { labelColor: v('--fg'), labelFontSize: 12 },
            view: { stroke: null },
            range: { category: colors },
            // Single-series marks follow the palette too (Vega's default steelblue reads as off-theme).
            mark: { color: colors[0] },
            bar: { color: colors[0], cornerRadiusEnd: 3 },
            line: { color: colors[0], strokeWidth: 2 },
            point: { color: colors[0] },
            area: { color: colors[0] },
          },
        }).then((r) => {
          if (!alive) {
            r.view.finalize();
            return;
          }
          viewRef.current = r.view as unknown as View;
        });
      })
      .catch((e) => alive && setErr(String(e?.message ?? e)));
    return () => {
      alive = false;
      viewRef.current?.finalize();
      viewRef.current = null;
    };
  }, [part, mode, palette]);

  const dl = async (t: 'png' | 'svg') => {
    const url = await viewRef.current?.toImageURL(t, 2);
    if (!url) return;
    const a = document.createElement('a');
    a.href = url;
    a.download = csvFilename(part.title).replace(/\.csv$/, `.${t}`);
    a.click();
  };
  const dlData = () => {
    downloadText(csvFilename(part.title), recordsToCsv(part.data?.values ?? []));
  };

  return (
    <figure className="group/chart my-4 overflow-hidden rounded-xl border border-line bg-elev" data-testid="chart" aria-label={part.alt}>
      <div className="flex items-center justify-between px-3 pt-2 pb-1">
        <figcaption className="truncate text-[13px] font-medium text-fg-muted">{part.title}</figcaption>
        <div className="flex items-center gap-0.5 opacity-0 transition-opacity group-hover/chart:opacity-100 group-focus-within/chart:opacity-100">
          <IconButton label="Download PNG" className="h-7 w-7" onClick={() => dl('png')}><Download size={15} /></IconButton>
          <button type="button" onClick={() => dl('svg')} className="rounded-md px-1.5 py-1 text-xs text-fg-muted hover:bg-hover">SVG</button>
          <button type="button" onClick={dlData} className="rounded-md px-1.5 py-1 text-xs text-fg-muted hover:bg-hover">CSV</button>
          {onShowSpec && <IconButton label="View spec" className="h-7 w-7" onClick={onShowSpec}><FileJson size={14} /></IconButton>}
          {onOpen && <IconButton label="Expand chart" className="h-7 w-7" onClick={onOpen}><Maximize2 size={14} /></IconButton>}
        </div>
      </div>
      <div className="px-3 pb-3" style={{ minHeight: full ? 360 : 300 }}>
        {err ? <div className="text-sm text-danger">Chart failed to render: {err}</div> : <div ref={ref} className="w-full" role="img" aria-label={part.alt} />}
      </div>
    </figure>
  );
}
