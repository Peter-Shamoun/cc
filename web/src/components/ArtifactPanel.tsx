import { useCallback, useEffect, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react';
import { X, Download, Maximize, Minimize, Printer } from 'lucide-react';
import clsx from 'clsx';
import type { CodePart } from '../lib/contract';
import { DataTable } from './parts/DataTable';
import { ChartView, buildSpec } from './parts/ChartView';
import { CodeBlock } from './parts/CodeBlock';
import { Markdown } from './parts/Markdown';
import { IconButton, CopyButton } from './ui';
import { csvFilename, downloadBlob, downloadText, tableToCsv, tableToTsv } from '../lib/csv';
import { useAdapter, useBlobUrl } from '../adapters/context';
import { slug, type Artifact } from './artifacts';
import { getPref, setPref } from '../lib/prefs';
import { useFocusTrap } from '../shell/primitives';
import { ErrorBoundary } from './ErrorBoundary';

const MIN_W = 360;
const PHONE = '(max-width: 767px)';
/** Desktop width is remembered as a fraction of the window (pref `artifactWidth`, a short UI flag, never content). */
function initialWidth() {
  const f = Number(getPref('artifactWidth'));
  return clampWidth(Math.round(window.innerWidth * (f > 0.2 && f < 0.8 ? f : 0.5)));
}
function clampWidth(w: number) {
  return Math.round(Math.max(MIN_W, Math.min(w, window.innerWidth * 0.75, window.innerWidth - 400)));
}
function usePhone() {
  const mq = () => (typeof window.matchMedia === 'function' ? window.matchMedia(PHONE) : null);
  const [phone, setPhone] = useState(() => mq()?.matches ?? false);
  useEffect(() => {
    const m = mq();
    if (!m) return;
    const on = () => setPhone(m.matches);
    m.addEventListener?.('change', on);
    return () => m.removeEventListener?.('change', on);
  }, []);
  return phone;
}

type Tab = { id: string; label: string };

function tabsFor(a: Artifact): Tab[] {
  switch (a.part.type) {
    case 'table': return [{ id: 'preview', label: 'Table' }, ...(sqlFor(a) ? [{ id: 'code', label: 'SQL' }] : [])];
    case 'chart': return [{ id: 'preview', label: 'Chart' }, { id: 'code', label: 'Spec' }, { id: 'data', label: 'Data' }];
    case 'code': return [{ id: 'code', label: 'Code' }];
    case 'image': return [{ id: 'preview', label: 'Preview' }];
    case 'report': return [{ id: 'preview', label: 'Preview' }, { id: 'code', label: 'Markdown' }];
  }
}

function sqlFor(a: Artifact): CodePart | undefined {
  if (a.part.type !== 'table' || !a.part.sqlPartId) return undefined;
  return a.related.find((p): p is CodePart => p.type === 'code' && p.id === (a.part as { sqlPartId?: string }).sqlPartId);
}

/**
 * The artifact panel inside its own error boundary: a render crash there shows a panel-shaped card (Try again, Close
 * panel) and leaves the chat usable. It retries by itself when another artifact or version is opened.
 */
export function ArtifactPanel(props: Parameters<typeof ArtifactPanelView>[0]) {
  return (
    <ErrorBoundary scope="section" name="ArtifactPanel" resetKey={props.artifact.part} onDismiss={props.onClose} dismissLabel="Close panel"
      fallbackClassName="flex h-full w-[min(22rem,100%)] shrink-0 flex-col items-start justify-center gap-0 border-l border-line bg-sidebar p-6 text-sm text-fg-muted">
      <ArtifactPanelView {...props} />
    </ErrorBoundary>
  );
}

/** Claude-style artifact panel (spec §4.10): resizable, segmented tabs, copy/download/full screen/close. */
function ArtifactPanelView({ artifact, onClose, versions, onVersion }: { artifact: Artifact; onClose: () => void; versions?: Artifact[]; onVersion?: (v: number) => void }) {
  const adapter = useAdapter();
  const [width, setWidthRaw] = useState(initialWidth);
  const phone = usePhone();
  const [fullPref, setFull] = useState(false);
  // On phones the panel is a full-screen sheet (Claude/ChatGPT mobile): no split view, no resize handle.
  const full = fullPref || phone;
  const tabs = tabsFor(artifact);
  // The chosen tab belongs to one artifact; a new artifact starts on its first tab (derived, no reset effect).
  const [picked, setPicked] = useState<{ artifact: Artifact; tab: string }>({ artifact, tab: tabs[0].id });
  const tab = picked.artifact === artifact ? picked.tab : tabs[0].id;
  const setTab = (id: string) => setPicked({ artifact, tab: id });
  const ref = useRef<HTMLElement>(null);
  useFocusTrap(ref, phone);
  const setWidth = useCallback((next: number | ((w: number) => number)) => {
    setWidthRaw((w) => clampWidth(typeof next === 'function' ? next(w) : next));
  }, []);
  const save = useCallback(() => setWidthRaw((w) => { setPref('artifactWidth', (w / window.innerWidth).toFixed(3)); return w; }), []);
  const drag = useRef(false);
  const onMove = useCallback((e: PointerEvent) => {
    if (!drag.current) return;
    setWidth(window.innerWidth - e.clientX);
  }, [setWidth]);
  useEffect(() => {
    const up = () => {
      if (!drag.current) return;
      drag.current = false; document.body.style.cursor = ''; document.body.style.userSelect = '';
      save();
    };
    const esc = (e: KeyboardEvent) => {
      if (e.key !== 'Escape' || e.defaultPrevented) return;
      if (fullPref && !phone) setFull(false); else onClose();
    };
    const resize = () => setWidth((w) => w);
    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup', up);
    window.addEventListener('keydown', esc);
    window.addEventListener('resize', resize);
    return () => { window.removeEventListener('pointermove', onMove); window.removeEventListener('pointerup', up); window.removeEventListener('keydown', esc); window.removeEventListener('resize', resize); };
  }, [onMove, onClose, save, setWidth, fullPref, phone]);
  const onSepKey = (e: ReactKeyboardEvent) => {
    const step = e.shiftKey ? 96 : 24;
    const map: Record<string, (w: number) => number> = {
      ArrowLeft: (w) => w + step, ArrowRight: (w) => w - step,
      Home: () => window.innerWidth, End: () => MIN_W,
    };
    const f = map[e.key];
    if (!f) return;
    e.preventDefault();
    setWidth(f);
    save();
  };

  const p = artifact.part;
  const sql = sqlFor(artifact);
  const codeText = p.type === 'code' ? p.code : p.type === 'chart' ? JSON.stringify(buildSpec(p), null, 2) : p.type === 'report' ? p.markdown : sql?.code ?? '';
  const imageExt = p.type === 'image' ? (p.mime === 'image/svg+xml' ? 'svg' : p.mime === 'image/webp' ? 'webp' : 'png') : '';
  const copyText = () => (tab === 'preview' && p.type === 'table' ? tableToTsv(p) : codeText);
  const download = async () => {
    if (p.type === 'image') { if (adapter) downloadBlob(`${slug(artifact.title)}.${imageExt}`, await adapter.fetchBlob(p.blobId)); return; }
    if (p.type === 'report') { downloadText(`${slug(artifact.title)}.md`, p.markdown, 'text/markdown'); return; }
    if (p.type === 'table' && tab === 'preview') downloadText(csvFilename(p.title), tableToCsv(p));
    else if (p.type === 'code') downloadText(p.title ?? 'snippet.sql', p.code, 'text/plain');
    else downloadText(csvFilename(artifact.title).replace(/\.csv$/, p.type === 'chart' ? '.vl.json' : '.sql'), codeText, 'text/plain');
  };

  return (
    <section ref={ref} style={full ? undefined : { width }} role={phone ? 'dialog' : 'complementary'} aria-modal={phone || undefined} data-sheet={phone || undefined}
      className={clsx('relative flex h-full shrink-0 flex-col border-l border-line bg-sidebar', full && 'fixed inset-0 z-40 w-full border-l-0', phone ? 'motion-safe:animate-[cc-rise_200ms_ease-out]' : 'motion-safe:animate-[cc-fade_150ms_ease-out]')}
      data-testid="artifact-panel" aria-label={`Artifact: ${artifact.title}`}>
      {!full && (
        <div role="separator" aria-orientation="vertical" aria-label="Resize panel" tabIndex={0}
          aria-valuemin={MIN_W} aria-valuemax={clampWidth(window.innerWidth)} aria-valuenow={width}
          onDoubleClick={() => { setWidth(Math.round(window.innerWidth * 0.5)); save(); }}
          onKeyDown={onSepKey}
          onPointerDown={() => { drag.current = true; document.body.style.cursor = 'col-resize'; document.body.style.userSelect = 'none'; }}
          className="absolute -left-[3px] top-0 z-10 h-full w-1.5 cursor-col-resize outline-none hover:bg-[var(--focus)]/40 focus-visible:bg-[var(--focus)]/60" />
      )}
      <header className="flex h-[52px] shrink-0 items-center justify-between gap-2 px-3">
        <div className="flex min-w-0 items-center gap-3">
          {tabs.length > 1 && (
            <div className="flex shrink-0 rounded-lg bg-muted p-0.5 text-xs" role="tablist">
              {tabs.map((t) => (
                <button key={t.id} role="tab" type="button" aria-selected={tab === t.id} onClick={() => setTab(t.id)} className={clsx('rounded-md px-2.5 py-1', tab === t.id ? 'bg-elev font-medium shadow-sm' : 'text-fg-muted hover:text-fg')}>{t.label}</button>
              ))}
            </div>
          )}
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{artifact.title}</div>
            <div className="text-xs text-fg-subtle"><span className="capitalize">{p.type === 'report' ? 'document' : p.type}</span>{artifact.version ? ` · v${artifact.version}` : ''}{versions && versions.length > 1 && artifact.version !== versions[versions.length - 1].version ? ' · older version' : ''}</div>
          </div>
          {versions && versions.length > 1 && onVersion && (
            <div className="flex shrink-0 items-center gap-0.5 rounded-lg bg-muted p-0.5 text-xs" role="group" aria-label="Versions" data-testid="artifact-versions">
              {versions.map((v) => (
                <button key={v.version} type="button" aria-pressed={v.version === artifact.version} onClick={() => onVersion(v.version ?? 1)}
                  className={clsx('rounded-md px-2 py-0.5 tabular-nums', v.version === artifact.version ? 'bg-elev font-medium shadow-sm' : 'text-fg-muted hover:text-fg')}>v{v.version}</button>
              ))}
            </div>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-0.5">
          {p.type !== 'image' && <CopyButton text={copyText} withText={!phone} label="Copy" />}
          <IconButton label={p.type === 'image' ? `Download ${imageExt.toUpperCase()}` : p.type === 'report' ? 'Download Markdown' : 'Download'} onClick={() => { void download(); }}><Download size={16} /></IconButton>
          {p.type === 'report' && !phone && <IconButton label="Print report" onClick={() => printReport(ref.current)}><Printer size={16} /></IconButton>}
          {!phone && <IconButton label={full ? 'Exit full screen' : 'Full screen'} onClick={() => setFull((f) => !f)}>{full ? <Minimize size={16} /> : <Maximize size={16} />}</IconButton>}
          <IconButton label="Close panel" onClick={onClose}><X size={18} /></IconButton>
        </div>
      </header>
      <div className={clsx('min-h-0 flex-1 overflow-auto pb-3', phone ? 'px-2' : 'px-3')}>
        {/* Content sits on the panel itself (no card-in-card); the table/chart keep their own hairline frame. */}
        <div className={clsx('min-h-full', phone ? 'p-1' : 'p-2')} data-testid="artifact-body">
          {tab === 'preview' && p.type === 'table' && <DataTable part={p} full />}
          {tab === 'preview' && p.type === 'chart' && <ChartView part={p} full />}
          {tab === 'preview' && p.type === 'image' && <PanelImage blobId={p.blobId} alt={p.alt} caption={p.caption} />}
          {tab === 'preview' && p.type === 'report' && (
            <article className="mx-auto max-w-[42rem] px-2 py-3 md:px-6 md:py-6" data-testid="artifact-report" data-report-doc>
              <Markdown text={p.markdown} />
            </article>
          )}
          {tab === 'data' && p.type === 'chart' && (
            <DataTable full part={{ type: 'table', id: `${p.id}-data`, title: `${p.title ?? 'Chart'} data`, columns: Object.keys(p.data?.values[0] ?? {}).map((k) => ({ key: k, label: k, type: typeof p.data?.values[0][k] === 'number' ? 'number' : 'string' })), rows: (p.data?.values ?? []).map((r) => Object.values(r)), rowCount: p.data?.values.length ?? 0 }} />
          )}
          {tab === 'code' && <CodeBlock code={codeText} language={p.type === 'code' ? p.language : p.type === 'chart' ? 'json' : p.type === 'report' ? 'markdown' : 'sql'} title={p.type === 'code' ? p.title : sql?.title} tall />}
        </div>
      </div>
    </section>
  );
}

/** Images (PNG/SVG/WebP plots) render through <img>, so an SVG's scripts and external refs never run. */
function PanelImage({ blobId, alt, caption }: { blobId: string; alt: string; caption?: string }) {
  const { url, err } = useBlobUrl(blobId);
  return (
    <figure className="m-0">
      {err ? <div className="flex h-64 items-center justify-center rounded-xl border border-line text-sm text-fg-subtle">Image unavailable</div>
        : url ? <img src={url} alt={alt} className="mx-auto max-w-full rounded-xl border border-line bg-white" data-testid="artifact-image" />
          : <div className="h-64 animate-pulse rounded-xl bg-muted" />}
      {caption && <figcaption className="mt-2 text-center text-xs text-fg-subtle">{caption}</figcaption>}
    </figure>
  );
}

/** Print just the report (same body-flag approach as "Print this answer"). */
function printReport(panel: HTMLElement | null) {
  if (!panel) return;
  document.body.setAttribute('data-print-report', '');
  const done = () => { document.body.removeAttribute('data-print-report'); window.removeEventListener('afterprint', done); };
  window.addEventListener('afterprint', done);
  window.print();
  setTimeout(done, 0);
}
