import { useEffect, useRef, useState } from 'react';
import { Download, Maximize2, FileJson } from 'lucide-react';
import type { ChartPart } from '../../lib/contract';
import { IconButton } from '../ui';
import { CHART_COLORS, useTheme } from '../../lib/theme';
import { csvFilename, downloadText } from '../../lib/csv';

type View = { finalize: () => void; toImageURL: (t: 'png' | 'svg', scale?: number) => Promise<string> };

/** Spec safety (§4.5): drop any data.url and inline the part's data. */
export function buildSpec(part: ChartPart): Record<string, unknown> {
  // Drop the spec's own data (only inline `part.data` is rendered, never data.url) and its $schema
  // (the backend pins v5; vega-embed would warn on every render under Vega-Lite 6).
  const { data: _ignored, $schema: _schema, ...rest } = part.spec as Record<string, unknown> & { data?: unknown; $schema?: unknown };
  void _ignored; void _schema;
  return { ...rest, data: { values: part.data?.values ?? [] } };
}

export function ChartView({ part, onOpen, full, onShowSpec }: { part: ChartPart; onOpen?: () => void; full?: boolean; onShowSpec?: () => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const viewRef = useRef<View | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const { mode, palette } = useTheme();
  useEffect(() => {
    let alive = true;
    import('vega-embed')
      .then(({ default: embed }) => {
        if (!alive || !ref.current) return;
        const dark = mode === 'dark';
        const css = getComputedStyle(document.documentElement);
        const v = (n: string) => css.getPropertyValue(n).trim();
        return embed(ref.current, buildSpec(part) as never, {
          actions: false,
          renderer: 'svg',
          config: {
            background: 'transparent',
            font: v('--font-sans') || 'sans-serif',
            axis: { labelColor: v('--fg-muted'), titleColor: v('--fg'), gridColor: dark ? '#3a3a3a' : '#ececec', domainColor: v('--border-strong'), tickColor: v('--border-strong'), labelFontSize: 12, titleFontSize: 12 },
            legend: { labelColor: v('--fg'), labelFontSize: 12 },
            view: { stroke: null },
            range: { category: CHART_COLORS[palette] },
            // Single-series marks follow the palette too (Vega's default steelblue reads as off-theme).
            mark: { color: CHART_COLORS[palette][0] },
            bar: { color: CHART_COLORS[palette][0], cornerRadiusEnd: 3 },
            line: { color: CHART_COLORS[palette][0], strokeWidth: 2 },
            point: { color: CHART_COLORS[palette][0] },
            area: { color: CHART_COLORS[palette][0] },
          },
        }).then((r) => {
          viewRef.current = r.view as unknown as View;
        });
      })
      .catch((e) => setErr(String(e?.message ?? e)));
    return () => {
      alive = false;
      viewRef.current?.finalize();
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
    const vals = part.data?.values ?? [];
    const keys = Object.keys(vals[0] ?? {});
    downloadText(csvFilename(part.title), [keys.join(','), ...vals.map((r) => keys.map((k) => JSON.stringify(r[k] ?? '')).join(','))].join('\r\n'));
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
