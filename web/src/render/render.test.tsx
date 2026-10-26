import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import { AdapterCtx } from '../adapters/context';
import type { AnalystAdapter } from '../adapters/types';
import { ChartView } from '../components/parts/ChartView';
import { DataTable } from '../components/parts/DataTable';
import { ImageView } from '../components/parts/misc';
import { loadMath, Markdown } from '../components/parts/Markdown';
import { recordsToCsv, tableToCsv, tableToTsv } from '../lib/csv';
import { CHART_COLORS, ThemeProvider, useTheme } from '../lib/theme';
import { buildSpec, chartRenderer } from './chartSpec';
import { chartFixtures } from './__fixtures__/charts';
import { FIXTURE_COUNT, renderFixtures } from './__fixtures__';
import { imageFixtures } from './__fixtures__/images';
import { markdownFixtures } from './__fixtures__/markdown';
import { tableFixtures } from './__fixtures__/tables';
import { safeHref } from './safeHref';

const embedState = vi.hoisted(() => ({
  calls: [] as { spec: unknown; opts: Record<string, unknown> }[],
  fail: false,
}));

vi.mock('vega-embed', () => ({
  default: (_el: unknown, spec: unknown, opts: Record<string, unknown>) => {
    embedState.calls.push({ spec, opts });
    if (embedState.fail) return Promise.reject(new Error('boom'));
    return Promise.resolve({ view: { finalize() {}, toImageURL: async () => '' } });
  },
}));

function assertNoActiveContent(container: HTMLElement) {
  expect(container.querySelector('script, iframe, object, embed, form, link, meta, base')).toBeNull();
  const walk = (el: Element) => {
    for (const attr of el.attributes) {
      const name = attr.name.toLowerCase();
      const value = attr.value.trim().toLowerCase();
      expect(name.startsWith('on')).toBe(false);
      if (name === 'src') {
        expect(value.startsWith('blob:') || value.startsWith('data:image/')).toBe(true);
        expect(value).not.toMatch(/^(javascript|vbscript|file|https?):/);
      }
      if (name === 'href' || name === 'xlink:href' || name === 'action' || name === 'formaction') {
        expect(value).not.toMatch(/^(javascript|data|vbscript|file|blob):/);
        expect(value.startsWith('//')).toBe(false);
      }
      if (name === 'style') {
        expect(value).not.toMatch(/javascript:|expression\s*\(|@import/);
      }
    }
    for (const child of el.children) walk(child);
  };
  walk(container);
}

async function withDownload(run: () => void): Promise<{ name: string; body: string }> {
  const blobs: Blob[] = [];
  const names: string[] = [];
  const prevCreate = URL.createObjectURL;
  const prevRevoke = URL.revokeObjectURL;
  const prevClick = HTMLAnchorElement.prototype.click;
  URL.createObjectURL = ((blob: Blob) => {
    blobs.push(blob);
    return 'blob:download';
  }) as typeof URL.createObjectURL;
  URL.revokeObjectURL = () => {};
  HTMLAnchorElement.prototype.click = function click() {
    names.push(this.download);
  };
  try {
    run();
    expect(blobs.length).toBeGreaterThan(0);
    return { name: names.at(-1) ?? '', body: await blobs.at(-1)!.text() };
  } finally {
    URL.createObjectURL = prevCreate;
    URL.revokeObjectURL = prevRevoke;
    HTMLAnchorElement.prototype.click = prevClick;
  }
}

afterEach(() => cleanup());

describe('render fixture corpus', () => {
  it('lists a unique torture case for every id', () => {
    expect(FIXTURE_COUNT).toBe(renderFixtures.length);
    expect(new Set(renderFixtures.map((f) => f.id)).size).toBe(FIXTURE_COUNT);
    expect(renderFixtures.filter((f) => f.kind === 'markdown').length).toBe(markdownFixtures.length);
    expect(FIXTURE_COUNT).toBeGreaterThanOrEqual(50);
  });
});

describe('safe links', () => {
  it('keeps http(s), mailto, hashes, and relative paths', () => {
    expect(safeHref('https://example.com/a?q=1')).toBe('https://example.com/a?q=1');
    expect(safeHref('mailto:ops@example.com')).toBe('mailto:ops@example.com');
    expect(safeHref('#rate')).toBe('#rate');
    expect(safeHref('/c/abc')).toBe('/c/abc');
    expect(safeHref('notes/q3')).toBe('notes/q3');
  });
  it('drops active and protocol-relative urls', () => {
    for (const href of ['javascript:alert(1)', 'JaVaScRiPt:alert(1)', 'data:text/html,hi', 'vbscript:msg', 'file:///etc/passwd', 'blob:https://x/1', '//evil.test/a', '\\\\evil\\a', ' \njavascript:alert(1)']) {
      expect(safeHref(href)).toBeNull();
    }
    expect(safeHref('https://example.com/\u202Eevil')).toBe('https://example.com/evil');
  });
});

describe('markdown fixtures', () => {
  // KaTeX is lazy in the app; preload it so these fixtures render math synchronously.
  beforeAll(async () => { await loadMath(); });
  it.each(markdownFixtures)('$id renders without active content', (fixture) => {
    const { container } = render(<Markdown text={fixture.markdown} streaming={fixture.streaming} />);
    assertNoActiveContent(container);
    const root = container.querySelector('[data-testid="markdown"]') as HTMLElement;
    expect(root).toHaveStyle({ overflowWrap: 'anywhere', unicodeBidi: 'isolate', maxWidth: '100%', minWidth: '0px' });
    if (fixture.streaming) expect(root.className).toContain('streaming-caret');
    if (fixture.contains) expect(container.textContent).toContain(fixture.contains);
    if (fixture.excludes) expect(container.textContent).not.toContain(fixture.excludes);
    if (fixture.math) expect(container.querySelector('.katex')).not.toBeNull();
    if (fixture.math === 'display') expect(container.querySelector('.katex-display')).not.toBeNull();
    if (fixture.rtl) expect(container.querySelector('[dir="rtl"]')).not.toBeNull();
    if (fixture.code) expect(container.textContent).toContain(fixture.code);
    if (fixture.id === 'half-table' || fixture.id === 'half-table-header-only' || fixture.id === 'mixed-partial') {
      expect(container.querySelector('table')).not.toBeNull();
      expect(container.querySelector('.overflow-x-auto')).not.toBeNull();
    }
    if (fixture.id === 'nested-lists') expect(container.querySelectorAll('ul').length).toBeGreaterThanOrEqual(3);
    if (fixture.id === 'nested-ordered') expect(container.querySelectorAll('ol').length).toBeGreaterThanOrEqual(2);
    if (fixture.id === 'safe-https-link') {
      const a = container.querySelector('a');
      expect(a?.getAttribute('href')).toBe('https://example.com/note?q=1');
      expect(a?.getAttribute('target')).toBe('_blank');
      expect(a?.getAttribute('rel')).toContain('noopener');
      expect(a?.getAttribute('rel')).toContain('noreferrer');
      expect(a?.getAttribute('rel')).toContain('nofollow');
    }
    if (fixture.id === 'remote-markdown-image' || fixture.id === 'html-img-onerror') expect(container.querySelector('img')).toBeNull();
    if (fixture.id === 'long-url' || fixture.id === 'long-line') expect((container.textContent ?? '').length).toBeGreaterThan(3000);
    if (fixture.id === 'task-list') {
      const boxes = container.querySelectorAll('input[type="checkbox"]');
      expect(boxes.length).toBeGreaterThan(0);
      for (const box of boxes) expect(box).toBeDisabled();
    }
    if (fixture.id === 'unclosed-bold') expect(container.querySelector('[data-streamdown="strong"]')?.textContent).toBe('sharply');
    if (fixture.id === 'unclosed-fence' || fixture.id === 'html-in-fence' || fixture.id === 'mixed-partial') {
      expect(container.querySelector('[data-testid="code-block"]')).not.toBeNull();
    }
  });
});

describe('table fixtures', () => {
  it.each(tableFixtures)('$id renders inside a stable scrollport', (fixture) => {
    const { container } = render(<DataTable part={fixture.part} />);
    assertNoActiveContent(container);
    expect(container.querySelector('[data-testid="data-table"]')).not.toBeNull();
    const scroll = container.querySelector('.overflow-auto') as HTMLElement;
    expect(scroll.className).toContain('max-h-[400px]');
    expect(container.querySelectorAll('th')).toHaveLength(fixture.part.columns.length);
    if (fixture.virtual) {
      expect(scroll).toHaveStyle({ height: '400px' });
      expect(scroll.getAttribute('data-virtual')).toBe('true');
      expect(container.querySelectorAll('tbody tr').length).toBeLessThan(80);
      expect(container.textContent).toContain('10,000');
    }
    if (fixture.id === 'empty-result') expect(container.textContent).toContain('Query returned 0 rows');
    if (fixture.id === 'duplicate-keys') {
      expect(container.textContent).toContain('Left');
      expect(container.textContent).toContain('Right');
      expect(container.textContent).toContain('42');
    }
    if (fixture.id === 'mixed-types') {
      const cells = [...container.querySelectorAll('tbody tr')[0].querySelectorAll('td')];
      expect(cells[0].className).not.toContain('text-right');
      expect(cells[1].className).toContain('text-right');
      expect(cells[2].className).toContain('text-right');
      expect(cells[2].className).toContain('tabular-nums');
      expect(cells[3].className).toContain('text-right');
      expect(container.textContent).toContain('$1,234.50');
      expect(container.textContent).toContain('12.5%');
      expect(container.textContent).toContain('1,000');
      expect(container.textContent).toContain('Yes');
      expect(container.textContent).toContain('<img src=x onerror="alert(1)">');
      expect(container.querySelector('img')).toBeNull();
      expect(container.textContent).toContain('-$2.00');
      expect(container.textContent).toContain('100.0%');
    }
    if (fixture.phi) {
      expect(container.textContent).not.toContain('SECRET99');
      expect(container.textContent).not.toContain('ZZ123456');
    }
  });

  it('sorts numeric columns with blanks after the numbers', () => {
    const part = tableFixtures.find((f) => f.id === 'sort-nulls')!.part;
    const { container } = render(<DataTable part={part} />);
    fireEvent.click(container.querySelector('thead button')!);
    const values = [...container.querySelectorAll('tbody td')].map((td) => td.textContent);
    expect(values.slice(0, 3)).toEqual(['-1', '2', '10']);
    expect(values.slice(3)).toEqual(['—', '—']);
  });

  it('exports hostile cells as RFC 4180 CSV and aligned TSV, without PHI', async () => {
    const part = tableFixtures.find((f) => f.id === 'csv-hostile')!.part;
    const csv = 'Note,Paid,Rate\r\n"say ""hi"",\nplease",1234.5,0.5\r\n,0,0\r\n\'=1+1,-2,1\r\na\tb,3,0.1';
    expect(tableToCsv(part)).toBe(csv);
    expect(tableToCsv(part)).not.toContain('SECRET99');
    expect(tableToTsv(part)).toBe('Note\tPaid\tRate\nsay "hi", please\t1234.5\t0.5\n\t0\t0\n\'=1+1\t-2\t1\na b\t3\t0.1');
    render(<DataTable part={part} />);
    const downloaded = await withDownload(() => fireEvent.click(screen.getAllByRole('button', { name: 'Download CSV' }).at(-1)!));
    expect(downloaded.body).toBe(csv);
    expect(downloaded.name).toMatch(/^claimclarity_csv_hostile_cells_\d{4}-\d{2}-\d{2}\.csv$/);
    expect(containerPhi(downloaded.body)).toBe(false);
  });

  it('exports the 10k-row fixture as one raw record per row', () => {
    const part = tableFixtures.find((f) => f.id === 'tall-10k')!.part;
    const lines = tableToCsv(part).split('\r\n');
    expect(lines).toHaveLength(10_001);
    expect(lines[0]).toBe('Payer,Count,Rate');
    expect(lines[1]).toBe(',0,');
    expect(lines[2]).toBe('p1,1,0.0001');
    expect(lines[6]).toBe('p5,5,');
    expect(lines.join('\n')).not.toContain('undefined');
    expect(lines.join('\n')).not.toContain('%');
  });

  it('exports fifty columns of raw values', () => {
    const part = tableFixtures.find((f) => f.id === 'wide-50')!.part;
    const header = tableToCsv(part).split('\r\n')[0].split(',');
    expect(header).toHaveLength(50);
    expect(header[0]).toBe('Col 0');
    expect(header[49]).toBe('Col 49');
    const { container } = render(<DataTable part={part} />);
    expect(container.textContent).toContain('$1,234.50');
    expect(container.textContent).toContain('12.5%');
    expect(container.textContent).toContain('—');
  });
});

function containerPhi(csv: string) {
  return csv.includes('SECRET') || csv.includes('ZZ123456');
}

describe('chart fixtures', () => {
  beforeEach(() => {
    embedState.calls.length = 0;
    embedState.fail = false;
    localStorage.clear();
  });

  it('strips remote data, usermeta, and active hrefs before embed', () => {
    for (const fixture of chartFixtures) {
      const spec = buildSpec(fixture.part);
      const json = JSON.stringify(spec);
      expect(json).not.toContain('evil.test');
      expect(json).not.toContain('javascript:');
      expect(json).not.toContain('"url"');
      expect(json).not.toContain('usermeta');
      expect(spec.data).toEqual({ values: fixture.part.data?.values ?? [] });
      if (fixture.canvas) expect(chartRenderer(fixture.part)).toBe('canvas');
      else expect(chartRenderer(fixture.part)).toBe('svg');
    }
    expect(buildSpec(chartFixtures.find((f) => f.id === 'invalid-spec')!.part)).toEqual({ data: { values: [] } });
  });

  it('exports chart rows with later columns and RFC 4180 quotes', () => {
    const values = chartFixtures.find((f) => f.id === 'nulls-in-series')!.part.data!.values;
    expect(recordsToCsv(values)).toBe('a,b,c\r\n"x,y",,\r\n,"z""z",2\r\n,0,true');
    expect(recordsToCsv([])).toBe('');
  });

  it.each(chartFixtures)('$id embeds without throwing', async (fixture) => {
    const { container } = render(
      <ThemeProvider>
        <ChartView part={fixture.part} />
      </ThemeProvider>,
    );
    await waitFor(() => expect(embedState.calls.length).toBeGreaterThan(0));
    assertNoActiveContent(container);
    expect(container.querySelector('[data-testid="chart"]')).not.toBeNull();
    expect(container.textContent).not.toContain('Chart failed');
    const call = embedState.calls.at(-1)!;
    expect(call.opts.renderer).toBe(fixture.canvas ? 'canvas' : 'svg');
    expect(call.opts.ast).toBe(true);
    const loader = call.opts.loader as { http: (uri: string) => Promise<string> };
    await expect(loader.http('https://evil.test/x.json')).rejects.toThrow(/blocked/);
    expect(JSON.stringify(call.spec)).not.toContain('evil.test');
    const plot = container.querySelector('figure > div:nth-child(2)') as HTMLElement;
    expect(plot.style.minHeight).toBe('300px');
  });

  it('switches palette and dark grid when the theme changes', async () => {
    const part = chartFixtures.find((f) => f.id === 'theme-switch')!.part;
    function Harness() {
      const theme = useTheme();
      return (
        <>
          <button type="button" onClick={theme.toggleMode}>toggle-mode</button>
          <button type="button" onClick={() => theme.setPalette(theme.palette === 'paper' ? 'neutral' : 'paper')}>toggle-palette</button>
          <ChartView part={part} />
        </>
      );
    }
    render(
      <ThemeProvider>
        <Harness />
      </ThemeProvider>,
    );
    await waitFor(() => expect(embedState.calls.length).toBe(1));
    const first = embedState.calls[0].opts.config as { range: { category: string[] }; axis: { gridColor: string } };
    expect(first.range.category[0]).toBe(CHART_COLORS.neutral[0]);
    expect(first.axis.gridColor).toBe('#ececec');
    expect((embedState.calls[0].opts.tooltip as { theme: string }).theme).toBe('light');

    fireEvent.click(screen.getByRole('button', { name: 'toggle-mode' }));
    await waitFor(() => expect(embedState.calls.length).toBe(2));
    const dark = embedState.calls[1].opts.config as { axis: { gridColor: string } };
    expect(dark.axis.gridColor).toBe('#3a3a3a');
    expect((embedState.calls[1].opts.tooltip as { theme: string }).theme).toBe('dark');

    fireEvent.click(screen.getByRole('button', { name: 'toggle-palette' }));
    await waitFor(() => expect(embedState.calls.length).toBe(3));
    const paper = embedState.calls[2].opts.config as { range: { category: string[] } };
    expect(paper.range.category[0]).toBe(CHART_COLORS.paper[0]);
  });

  it('downloads chart CSV from the button', async () => {
    const part = chartFixtures.find((f) => f.id === 'nulls-in-series')!.part;
    render(
      <ThemeProvider>
        <ChartView part={part} />
      </ThemeProvider>,
    );
    const downloaded = await withDownload(() => fireEvent.click(screen.getAllByRole('button', { name: 'CSV' }).at(-1)!));
    expect(downloaded.body).toBe('a,b,c\r\n"x,y",,\r\n,"z""z",2\r\n,0,true');
    expect(downloaded.name).toMatch(/^claimclarity_nulls_in_series_\d{4}-\d{2}-\d{2}\.csv$/);
  });

  it('shows the embed error instead of throwing', async () => {
    embedState.fail = true;
    const part = chartFixtures.find((f) => f.id === 'empty-values')!.part;
    render(
      <ThemeProvider>
        <ChartView part={part} />
      </ThemeProvider>,
    );
    expect(await screen.findByText(/Chart failed to render: boom/)).toBeInTheDocument();
  });
});

describe('image fixtures', () => {
  beforeEach(() => {
    URL.createObjectURL = vi.fn(() => 'blob:fixture');
    URL.revokeObjectURL = vi.fn();
  });

  function adapterFor(missing: boolean): AnalystAdapter {
    return {
      name: 'fixture',
      async *streamTurn() {},
      fetchBlob: async (id) => {
        if (missing || id.startsWith('missing')) throw new Error('missing');
        return new Blob([Uint8Array.from([137, 80, 78, 71])], { type: 'image/png' });
      },
    };
  }

  it.each(imageFixtures)('$id keeps a capped box and no active content', async (fixture) => {
    const { container } = render(
      <AdapterCtx.Provider value={adapterFor(!!fixture.missing)}>
        <ImageView part={fixture.part} />
      </AdapterCtx.Provider>,
    );
    const frame = container.querySelector('[data-testid="image-frame"]') as HTMLElement;
    expect(frame).toHaveStyle({ width: `${fixture.box.width}px`, height: `${fixture.box.height}px` });
    expect(fixture.box.width).toBeLessThanOrEqual(768);
    expect(fixture.box.height).toBeLessThanOrEqual(480);
    if (fixture.missing) {
      expect(await screen.findByText('Image unavailable')).toBeInTheDocument();
      expect(container.querySelector('[data-testid="image-frame"]')).toHaveStyle({ width: `${fixture.box.width}px`, height: `${fixture.box.height}px` });
    } else {
      const img = await screen.findByRole('img', { name: fixture.part.alt });
      expect(img.getAttribute('src')).toBe('blob:fixture');
      expect(img.getAttribute('alt')).toBe(fixture.part.alt);
    }
    assertNoActiveContent(container);
    if (fixture.part.caption) expect(container.textContent).toContain(fixture.part.caption);
    expect(container.querySelector('script')).toBeNull();
  });
});
