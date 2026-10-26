import { useEffect, useMemo, useRef, useState } from 'react';
import { Check, Copy, CornerDownRight, Download, Maximize2, FileJson, X } from 'lucide-react';
import type { ChartPart } from '../../lib/contract';
import { IconButton } from '../ui';
import { ErrorBoundary } from '../ErrorBoundary';
import { onceOk } from '../../lib/onceOk';
import { tooltipHtml } from '../../security/trustedTypes';
import { CHART_COLORS, useTheme } from '../../lib/theme';
import { csvFilename, downloadText, recordsToCsv } from '../../lib/csv';
import { buildSpec, chartRenderer, readableBars, utcDates, withTooltips } from '../../render/chartSpec';
import { parsePlot } from '../../contract/plot';

export { buildSpec };

/** One shared lazy load: remounts (virtual list, reopened chats) redraw without waiting on the import again. */
// A failed load is retried on the next chart (lib/onceOk), not cached.
const loadVega = onceOk(() => Promise.all([import('vega-embed'), import('vega')]));
/** Idle warm-up from main.tsx (lib/idlePrefetch): the first chart then draws without loading or compiling Vega. */
const yieldToMain = (): Promise<void> => new Promise((r) => setTimeout(r, 0));

export const preloadCharts = (): Promise<void> => loadVega().then(() => undefined);

type View = { finalize: () => void; toImageURL: (t: 'png' | 'svg', scale?: number) => Promise<string>; addEventListener?: (type: string, h: (e: unknown, item?: { datum?: unknown } | null) => void) => void };

type Enc = { field?: unknown; type?: unknown; timeUnit?: unknown };
/**
 * The category a clicked mark stands for (the bar's x/y label, else its series), for "Ask about …". Only nominal or
 * ordinal fields (then temporal), so a click never turns a measure like 0.0898 into a question. Capped at 60 chars.
 */
export function datumLabel(spec: Record<string, unknown>, datum: unknown): string | null {
  if (!datum || typeof datum !== 'object') return null;
  // Layered specs (rule/text overlays from `plot`) keep the bar encoding on a layer.
  const layer = Array.isArray(spec.layer) ? (spec.layer as { encoding?: unknown }[]).find((l) => l?.encoding)?.encoding : undefined;
  const enc = { ...(layer as object | undefined), ...(spec.encoding as object | undefined) } as Record<string, Enc | undefined>;
  const d = datum as Record<string, unknown>;
  const pick = (types: string[]) => {
    for (const ch of ['x', 'y', 'color', 'theta']) {
      const e = enc[ch];
      if (e && typeof e.field === 'string' && types.includes(String(e.type)) && d[e.field] != null && String(d[e.field]).trim()) {
        const v = d[e.field];
        return (v instanceof Date ? v.toISOString().slice(0, 10) : String(v)).trim();
      }
    }
    return null;
  };
  const v = pick(['nominal', 'ordinal']) ?? pick(['temporal']);
  return v ? (v.length > 60 ? v.slice(0, 59) + '…' : v) : null;
}

/** PNG blob from a data URL (Vega's toImageURL), for the clipboard. Decoded in place: fetch() of a data: URL is
 * blocked by the CSP (`connect-src 'self'`). */
export function dataUrlBlob(url: string): Blob {
  const match = /^data:([^;,]+);base64,(.*)$/.exec(url);
  if (!match) throw new Error('Chart image is not a base64 data URL');
  const binary = atob(match[2]);
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return new Blob([bytes], { type: match[1] });
}
export const canCopyImage = (): boolean => typeof ClipboardItem !== 'undefined' && !!navigator.clipboard?.write;

/**
 * Refuse http(s) and file loads. Inline `values` never need the loader (spec §4.5, §6.11).
 * `vega` is passed in from the lazy import so it stays out of the initial bundle.
 */
function remoteBlockedLoader(createVegaLoader: typeof import('vega').loader) {
  const loader = createVegaLoader();
  const reject = () => Promise.reject(new Error('Remote chart data is blocked'));
  loader.http = reject;
  loader.file = reject;
  return loader;
}

type ChartViewProps = { part: ChartPart; onOpen?: () => void; full?: boolean; onShowSpec?: () => void; /** Click a bar → "Ask about …" sends a follow-up (answer surface only). */ onAsk?: (text: string) => void };

/** A chart that fails to render (bad spec, renderer bug) becomes an inline "Try again" card; the rest of the answer stays. */
export function ChartView(props: ChartViewProps) {
  // 1.18.0: validate a strict plot before rendering; an invalid one is not drawn (the table stays). Contract §16.1.
  const plotOk = useMemo(() => props.part.plot === undefined || parsePlot(props.part.plot) !== null, [props.part.plot]);
  if (!plotOk) return null;
  return (
    <ErrorBoundary scope="section" name="ChartView" resetKey={props.part}>
      <ChartViewInner {...props} />
    </ErrorBoundary>
  );
}

function ChartViewInner({ part, onOpen, full, onShowSpec, onAsk }: ChartViewProps) {
  const [picked, setPicked] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const askRef = useRef(onAsk);
  useEffect(() => { askRef.current = onAsk; }, [onAsk]);
  const ref = useRef<HTMLDivElement>(null);
  const viewRef = useRef<View | null>(null);
  // Memory: Vega keeps module-level references to the last SVG it drew, and that SVG reaches the View, whose `_el` is
  // the element we embed into. If that were our React node, its parent chain would keep the whole unmounted
  // thread window alive (~3k detached nodes after leaving a 25-turn chat). So Vega draws into a host div we
  // own and detach on unmount; what Vega still holds then ends at that host.
  const hostRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => () => { hostRef.current?.remove(); hostRef.current = null; }, []);
  const [err, setErr] = useState<string | null>(null);
  // QA-A2: keep a skeleton at the final height until vega has drawn, so the card never shows an empty frame.
  const [ready, setReady] = useState(false);
  const { mode, palette } = useTheme();
  // QA-A2: redraw only when the spec really changes, not on every new part object identity
  // (stream updates, history rehydrate), so a finished chart never drops back to its placeholder.
  const specKey = useMemo(() => JSON.stringify(readableBars(withTooltips(utcDates(buildSpec(part))))), [part]);
  // Reserve the plot's own height (+ axis room) so the card does not jump or leave a gap once drawn.
  const reserve = useMemo(() => {
    const h = (JSON.parse(specKey) as { height?: unknown }).height;
    return typeof h === 'number' ? Math.max(160, h + 56) : 300;
  }, [specKey]);
  useEffect(() => {
    let alive = true;
    // ThemeProvider's effect runs after this one. Set the tokens first so the
    // first embed already matches the active mode and palette.
    const root = document.documentElement;
    root.classList.toggle('dark', mode === 'dark');
    root.dataset.palette = palette;
    const done = (r: { view: unknown }) => {
      if (!alive) {
        (r.view as View).finalize();
        return;
      }
      viewRef.current = r.view as unknown as View;
      viewRef.current.addEventListener?.('click', (_e, item) => {
        if (!askRef.current) return;
        setPicked(datumLabel(JSON.parse(specKey) as Record<string, unknown>, item?.datum));
      });
      setReady(true);
    };
    loadVega()
      .then(async ([embedMod, { loader: createVegaLoader }]) => {
        const embed = embedMod.default;
        // Read vegaLite only if the module exposes it (some vega-embed builds and test doubles don't): then one embed, as before.
        const vegaLite = 'vegaLite' in embedMod ? embedMod.vegaLite : undefined;
        if (!alive || !ref.current) return;
        const dark = mode === 'dark';
        const css = getComputedStyle(root);
        const v = (n: string) => css.getPropertyValue(n).trim();
        const colors = CHART_COLORS[palette];
        // QA-A3: on narrow cards put the legend under the plot and let it wrap, so series never clip.
        const narrow = (ref.current.clientWidth || window.innerWidth) < 480;
        const config = {
          background: 'transparent',
          font: v('--font-sans') || 'sans-serif',
          axis: { labelColor: v('--fg-muted'), titleColor: v('--fg'), gridColor: dark ? '#3a3a3a' : '#ececec', domainColor: v('--border-strong'), tickColor: v('--border-strong'), labelFontSize: 12, titleFontSize: 12 },
          legend: narrow
            ? { labelColor: v('--fg'), labelFontSize: 11, orient: 'bottom' as const, direction: 'vertical' as const, columns: 1, symbolSize: 60, labelLimit: 0, titleFontSize: 11 }
            : { labelColor: v('--fg'), labelFontSize: 12 },
          view: { stroke: null },
          range: { category: colors },
          // Single-series marks follow the palette too (Vega's default steelblue reads as off-theme).
          mark: { color: colors[0] },
          bar: { color: colors[0], cornerRadiusEnd: 3 },
          line: { color: colors[0], strokeWidth: 2 },
          point: { color: colors[0] },
          area: { color: colors[0] },
        };
        const base = {
          actions: false,
          renderer: chartRenderer(part),
          // vega-interpreter: no Function/eval on model-authored expressions (spec §6.9) and no 'unsafe-eval' in the CSP.
          ast: true,
          loader: remoteBlockedLoader(createVegaLoader),
          // Strict CSP (no inline <style>): no injected stylesheets; the tooltip is themed in index.css (#vg-tooltip-element).
          defaultStyle: false,
          // Trusted Types: tooltip HTML comes from our escaping formatter, minted by the cc-html policy (security/trustedTypes.ts).
          tooltip: { theme: dark ? 'dark' : 'light', disableDefaultStyle: true, formatTooltip: tooltipHtml },
        };
        // 6x CPU: compile + parse + first render was one ~400 ms task mid-stream, with keystrokes stuck behind it.
        // Compile Vega-Lite here, yield to the main thread, then hand Vega the compiled spec: two shorter tasks.
        // Same result as embed's own vega-lite path (config applied by the compiler, Vega parses with {}, no hover).
        // Swap in a fresh host right before drawing (embed clears its element the same way), so a theme switch
        // keeps the old chart on screen until the new one is ready.
        const host = (): HTMLDivElement => {
          const h = document.createElement('div');
          ref.current!.replaceChildren(h);
          hostRef.current = h;
          return h;
        };
        if (vegaLite?.compile) {
          const vg = vegaLite.compile(JSON.parse(specKey), { config }).spec;
          await yieldToMain();
          if (!alive || !ref.current) return;
          return embed(host(), vg, { ...base, mode: 'vega', config: {}, hover: false }).then(done);
        }
        return embed(host(), JSON.parse(specKey), { ...base, config }).then(done);
      })
      .catch((e) => alive && setErr(String(e?.message ?? e)));
    return () => {
      alive = false;
      viewRef.current?.finalize();
      viewRef.current = null;
    };
  }, [specKey, mode, palette]); // eslint-disable-line react-hooks/exhaustive-deps

  const dl = async (t: 'png' | 'svg') => {
    const url = await viewRef.current?.toImageURL(t, 2);
    if (!url) return;
    const a = document.createElement('a');
    a.href = url;
    a.download = csvFilename(part.title).replace(/\.csv$/, `.${t}`);
    a.click();
  };
  const copyImage = async () => {
    try {
      const url = await viewRef.current?.toImageURL('png', 2);
      if (!url) return;
      await navigator.clipboard.write([new ClipboardItem({ 'image/png': dataUrlBlob(url) })]);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard refused (permissions, insecure context): the PNG download is right next to it */ }
  };
  const dlData = () => {
    downloadText(csvFilename(part.title), recordsToCsv(part.data?.values ?? []));
  };

  return (
    <figure className="group/chart my-4 overflow-hidden rounded-xl border border-line bg-elev" data-testid="chart" aria-label={part.alt}>
      <div className="flex items-start justify-between gap-2 px-3 pt-2 pb-1">
        <figcaption className="min-w-0 break-words text-[13px] font-medium leading-snug text-fg-muted">{part.title}</figcaption>
        <div className="flex shrink-0 items-center gap-0.5 opacity-0 group-hover/chart:opacity-100 group-focus-within/chart:opacity-100 [@media(hover:none)]:opacity-100">
          {canCopyImage() && <IconButton label={copied ? 'Copied' : 'Copy as image'} className="h-7 w-7" onClick={copyImage} disabled={!ready}>{copied ? <Check size={14} /> : <Copy size={14} />}</IconButton>}
          <IconButton label="Download PNG" className="h-7 w-7" onClick={() => dl('png')}><Download size={15} /></IconButton>
          <button type="button" onClick={() => dl('svg')} className="rounded-md px-1.5 py-1 text-xs text-fg-muted hover:bg-hover">SVG</button>
          <button type="button" onClick={dlData} className="rounded-md px-1.5 py-1 text-xs text-fg-muted hover:bg-hover">CSV</button>
          {onShowSpec && <IconButton label="View spec" className="h-7 w-7" onClick={onShowSpec}><FileJson size={14} /></IconButton>}
          {onOpen && <IconButton label="Expand chart" className="h-7 w-7" onClick={onOpen}><Maximize2 size={14} /></IconButton>}
        </div>
      </div>
      <div className="px-3 pb-3" style={{ minHeight: full ? 360 : reserve }}>
        {err ? (
          <div className="text-sm text-danger">Chart failed to render: {err}</div>
        ) : (
          <div className="relative">
            {!ready && <ChartSkeleton height={full ? 360 : reserve - 20} />}
            <div ref={ref} className={'w-full transition-opacity duration-300 motion-reduce:transition-none ' + (ready ? 'opacity-100' : 'opacity-0')} role="img" aria-label={part.alt} aria-busy={!ready} />
          </div>
        )}
      </div>
      {picked && onAsk && (
        <div className="flex items-center gap-1 border-t border-line px-2 py-1 font-sans text-sm" data-testid="chart-ask">
          <button type="button" className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-1.5 py-1 text-left text-fg-muted hover:text-fg"
            onClick={() => { onAsk(`Tell me more about ${picked}${part.title ? ` in "${part.title}"` : ''}`); setPicked(null); }}>
            <CornerDownRight size={14} className="shrink-0 text-fg-subtle" aria-hidden />
            <span className="min-w-0 truncate">Ask about {picked}</span>
          </button>
          <IconButton label="Dismiss" className="h-7 w-7" onClick={() => setPicked(null)}><X size={14} /></IconButton>
        </div>
      )}
    </figure>
  );
}

/** Neutral bar-silhouette placeholder at the chart's final height (QA-A2). */
function ChartSkeleton({ height }: { height: number }) {
  const bars = [62, 78, 45, 88, 70, 54, 82, 38];
  return (
    <div className="absolute inset-x-0 top-0 flex items-end gap-[3%] px-6 pb-6 pt-4" style={{ height }} aria-hidden data-testid="chart-skeleton">
      {bars.map((h, i) => (
        <div key={i} className="skeleton flex-1 rounded-t-md" style={{ height: `${h}%` }} />
      ))}
    </div>
  );
}
