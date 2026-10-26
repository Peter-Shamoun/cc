import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { ArtifactPanel } from './ArtifactPanel';
import { AdapterCtx } from '../adapters/context';
import { artifactVersions, autoOpenIds, contentOf, nextAutoOpen, slug, userIsTyping } from './artifacts';
import type { AnalystAdapter } from '../adapters/types';
import type { ArtifactPart } from '../lib/contract';

afterEach(() => { cleanup(); localStorage.clear(); document.body.innerHTML = ''; });
window.matchMedia = vi.fn().mockImplementation((q: string) => ({ matches: false, media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as unknown as typeof window.matchMedia;

const chart = (v: number, id: string, mark = 'bar'): ArtifactPart => ({
  type: 'artifact', id, artifactId: 'af_1', version: v, kind: 'chart', title: `Paid by month v${v}`,
  content: { type: 'chart', id: `${id}c`, library: 'vega-lite', spec: { mark }, data: { name: 'result', values: [{ m: '2026-01', paid: 1 }] }, alt: 'Paid by month' } as never,
});
const report: ArtifactPart = { type: 'artifact', id: 'p_r', artifactId: 'af_2', version: 1, kind: 'report', title: 'Summary report', content: { markdown: '# Summary report\n\n12 rows from `sem.claims`.\n\n| Month | Paid |\n| --- | --- |\n| 2026-01 | 1 |' }, autoOpen: true };
const msgs = [
  { id: 'a1', role: 'assistant', parts: [chart(1, 'p1'), report] },
  { id: 'u2', role: 'user', parts: [] },
  { id: 'a2', role: 'assistant', parts: [chart(2, 'p2', 'line')] },
];

describe('artifact versions + autoOpen (contract 1.9 §16)', () => {
  it('collects every version across messages, ascending', () => {
    const vs = artifactVersions(msgs, 'af_1');
    expect(vs.map((v) => v.version)).toEqual([1, 2]);
    expect(vs[1].part.type).toBe('chart');
    expect(artifactVersions(msgs, 'nope')).toEqual([]);
  });
  it('report content becomes a markdown doc', () => {
    expect(contentOf(report)).toMatchObject({ type: 'report', markdown: expect.stringContaining('# Summary report') });
  });
  it('autoOpen fires once per part', () => {
    const seen = new Set<string>();
    const first = nextAutoOpen(msgs, seen);
    expect(first?.partId).toBe('p_r');
    autoOpenIds(msgs).forEach((id) => seen.add(id));
    expect(nextAutoOpen(msgs, seen)).toBeNull();
  });
  it('QA-A27: no autoOpen on phones/tablets (coarse pointer or < 768px); the card opens on tap instead', () => {
    const mm = window.matchMedia;
    const w = window.innerWidth;
    try {
      window.matchMedia = vi.fn().mockImplementation((q: string) => ({ matches: q.includes('coarse'), media: q, addEventListener: vi.fn(), removeEventListener: vi.fn() })) as unknown as typeof window.matchMedia;
      expect(nextAutoOpen(msgs, new Set())).toBeNull();
      window.matchMedia = mm;
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
      expect(nextAutoOpen(msgs, new Set())).toBeNull();
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
      expect(nextAutoOpen(msgs, new Set())?.partId).toBe('p_r');
    } finally {
      window.matchMedia = mm;
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: w });
    }
  });
  it('typing in a non-empty textarea blocks autoOpen; an empty composer does not', () => {
    const ta = document.createElement('textarea');
    document.body.appendChild(ta);
    ta.focus();
    expect(userIsTyping()).toBe(false);
    ta.value = 'how many';
    expect(userIsTyping()).toBe(true);
  });
  it('slug is filename safe', () => expect(slug('Paid / month: Q3!')).toBe('paid_month_q3'));
});

describe('ArtifactPanel report + versions + images', () => {
  it('report: Preview renders markdown as a document, Markdown tab shows source, download is .md', () => {
    const a = { part: contentOf(report), title: report.title, version: 1, artifactId: 'af_2', related: [] };
    render(<ArtifactPanel artifact={a} onClose={() => {}} />);
    const doc = screen.getByTestId('artifact-report');
    expect(doc.querySelector('h1')?.textContent).toBe('Summary report');
    expect(doc.querySelector('table')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Download Markdown' })).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Print report' })).toBeTruthy();
    fireEvent.click(screen.getByRole('tab', { name: 'Markdown' }));
    expect(screen.getByTestId('artifact-panel').textContent).toContain('# Summary report');
  });
  it('version chips switch versions and mark older ones', () => {
    const vs = artifactVersions(msgs, 'af_1');
    const onVersion = vi.fn();
    render(<ArtifactPanel artifact={vs[0]} versions={vs} onVersion={onVersion} onClose={() => {}} />);
    const group = screen.getByTestId('artifact-versions');
    expect(group.querySelectorAll('button')).toHaveLength(2);
    expect(screen.getByRole('button', { name: 'v1' }).getAttribute('aria-pressed')).toBe('true');
    expect(screen.getByTestId('artifact-panel').textContent).toContain('older version');
    fireEvent.click(screen.getByRole('button', { name: 'v2' }));
    expect(onVersion).toHaveBeenCalledWith(2);
  });
  it('a single version shows no chips', () => {
    const vs = artifactVersions(msgs, 'af_2');
    render(<ArtifactPanel artifact={vs[0]} versions={vs} onVersion={() => {}} onClose={() => {}} />);
    expect(screen.queryByTestId('artifact-versions')).toBeNull();
  });
  it('SVG plot renders through <img> with alt text and downloads as .svg', async () => {
    const svg = new Blob(['<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>'], { type: 'image/svg+xml' });
    const adapter = { fetchBlob: vi.fn().mockResolvedValue(svg) } as unknown as AnalystAdapter;
    URL.createObjectURL = vi.fn(() => 'blob:x');
    URL.revokeObjectURL = vi.fn();
    const a = { title: 'Denial trend', related: [], part: { type: 'image' as const, id: 'i1', blobId: 'b1', mime: 'image/svg+xml' as const, width: 600, height: 300, alt: 'Line chart of denial rate by month', caption: 'Synthetic data' } };
    render(<AdapterCtx.Provider value={adapter}><ArtifactPanel artifact={a} onClose={() => {}} /></AdapterCtx.Provider>);
    const img = await screen.findByTestId('artifact-image');
    expect(img.tagName).toBe('IMG');
    expect(img.getAttribute('alt')).toBe('Line chart of denial rate by month');
    expect(document.querySelector('script')).toBeNull();
    expect(screen.getByText('Synthetic data')).toBeTruthy();
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    fireEvent.click(screen.getByRole('button', { name: 'Download SVG' }));
    await waitFor(() => expect(click).toHaveBeenCalled());
    click.mockRestore();
  });
});
