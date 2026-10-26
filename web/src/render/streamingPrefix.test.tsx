import { cleanup, render } from '@testing-library/react';
import { afterEach, beforeAll, describe, expect, it } from 'vitest';
import { loadMath, Markdown, preloadMarkdown } from '../components/parts/Markdown';
import { markdownFixtures } from './__fixtures__/markdown';
import { streamingDocs } from './__fixtures__/streaming';
import { repairStreamingTail, completeTex } from '../components/parts/markdownText';

afterEach(() => cleanup());

/** Cut points: every char for short docs, otherwise ~60 evenly spaced cuts plus every char of the last 12. */
function cuts(s: string): number[] {
  if (s.length <= 120) return Array.from({ length: s.length }, (_, i) => i + 1);
  const step = Math.max(1, Math.floor(s.length / 60));
  const out = new Set<number>();
  for (let i = step; i < s.length; i += step) out.add(i);
  for (let i = Math.max(1, s.length - 12); i <= s.length; i++) out.add(i);
  // Also cut right after every newline / fence / pipe, where glitches live.
  const limit = s.length > 3000 ? 400 : s.length;
  for (let i = 0; i < limit; i++) if ('\n`|$'.includes(s[i])) out.add(i + 1);
  return [...out].sort((a, b) => a - b);
}

/** Visible text outside code blocks and math. */
function proseText(root: HTMLElement): string {
  const clone = root.cloneNode(true) as HTMLElement;
  clone.querySelectorAll('[data-testid="code-block"], pre, code, .katex, .katex-display').forEach((n) => n.remove());
  return clone.textContent ?? '';
}

export function glitches(root: HTMLElement): string[] {
  const t = proseText(root);
  const out: string[] = [];
  if (/```|~~~/.test(t)) out.push('raw fence');
  if (/\|\s*:?-{3,}/.test(t)) out.push('raw table rule');
  if (/\$\$/.test(t)) out.push('raw $$');
  if (/\*\*/.test(t)) out.push('raw **');
  if (root.querySelector('.katex-error')) out.push('katex error');
  if (root.querySelector('script, iframe, object, embed')) out.push('active content');
  return out;
}

describe('streaming prefixes of the torture corpus', () => {
  beforeAll(async () => { await Promise.all([loadMath(), preloadMarkdown()]); });
  const docs = [...markdownFixtures.filter((f) => !f.id.startsWith('html-') && !/currency|bidi|link|math-href|remote/.test(f.id)).map((f) => ({ id: f.id, markdown: f.markdown })), ...streamingDocs];
  // A line the model itself left unclosed (not the streaming tail) shows as written, like a final render would.
  const ALLOW: Record<string, string[]> = { 'mixed-partial': ['raw **'] };
  it.each(docs)('$id: every prefix renders without glitches', ({ id, markdown }) => {
    const bad: string[] = [];
    for (const n of cuts(markdown)) {
      const prefix = markdown.slice(0, n);
      const { container, unmount } = render(<Markdown text={prefix} streaming />);
      const g = glitches(container.querySelector('[data-testid="markdown"]') as HTMLElement).filter((x) => !ALLOW[id]?.includes(x));
      if (g.length) bad.push(`${n}: ${g.join(',')} :: ${JSON.stringify(prefix.slice(-40))}`);
      unmount();
    }
    expect(bad.slice(0, 8)).toEqual([]);
  }, 60_000);
});

describe('repairStreamingTail', () => {
  it('drops a dangling emphasis opener at the tail only', () => {
    expect(repairStreamingTail('Denials rose **')).toBe('Denials rose ');
    expect(repairStreamingTail('a **b**')).toBe('a **b**');
    expect(repairStreamingTail('x ~~')).toBe('x ');
    expect(repairStreamingTail('2 * 3')).toBe('2 * 3');
  });
  it('completes a half-typed delimiter row from the header, keeping typed alignments', () => {
    expect(repairStreamingTail('| A | B | C |\n| --')).toBe('| A | B | C |\n| --- | --- | --- |\n');
    expect(repairStreamingTail('| A | B |\n| ---: | -')).toBe('| A | B |\n| ---: | --- |\n');
    expect(repairStreamingTail('| A | B |\n|')).toBe('| A | B |\n| --- | --- |\n');
  });
  it('leaves body rows, prose dashes and open code fences alone', () => {
    expect(repairStreamingTail('| A |\n| --- |\n| 1 |\n| --')).toBe('| A |\n| --- |\n| 1 |\n| --');
    expect(repairStreamingTail('Intro\n---')).toBe('Intro\n---');
    expect(repairStreamingTail('```md\n| A |\n| --')).toBe('```md\n| A |\n| --');
    expect(repairStreamingTail('```\nx **')).toBe('```\nx **');
  });

  it('trims an open display-math block back to its last complete point', () => {
    expect(repairStreamingTail('Rate:\n\n$$\n\\frac{a}{b')).toBe('Rate:\n\n');
    expect(repairStreamingTail('Rate:\n\n$$ x = \\frac{1')).toBe('Rate:\n\n$$ x = ');
    expect(repairStreamingTail('$$\n\\int_0^1 x \\, dx')).toBe('$$\n\\int_0^1 x \\, dx');
    expect(completeTex('x^')).toBe('x');
    expect(completeTex('a + \\fra')).toBe('a + ');
    expect(completeTex('a = \\frac{n}')).toBe('a = ');
    expect(completeTex('a = \\frac{n}{d}')).toBe('a = \\frac{n}{d}');
    expect(completeTex('\\sqrt{x} + \\left( y')).toBe('\\sqrt{x} + ');
    expect(completeTex('\\begin{aligned} a &= b')).toBe('');
    expect(completeTex('w_{10} d_')).toBe('w_{10} d');
    expect(repairStreamingTail('Rate:\n\n$$\nx\n$$\n\nwhere')).toBe('Rate:\n\n$$\nx\n$$\n\nwhere');
    expect(repairStreamingTail('A $$x$$ inline')).toBe('A $$x$$ inline');
    expect(repairStreamingTail('$$x$$\n\nmore')).toBe('$$x$$\n\nmore');
    // Left open by the model and followed by more text: render as written.
    expect(repairStreamingTail('$$\nx\n\nNext paragraph')).toBe('$$\nx\n\nNext paragraph');
    // Inside a code fence it is just text.
    expect(repairStreamingTail('```\n$$\nx')).toBe('```\n$$\nx');
  });
});
