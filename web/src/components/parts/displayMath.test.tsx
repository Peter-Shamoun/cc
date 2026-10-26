import { cleanup, render, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { Markdown, displayMath } from './Markdown';

afterEach(cleanup);

describe('display math (QA-S9)', () => {
  it('splits a one-line $$…$$ onto its own fence lines', () => {
    expect(displayMath('Rate:\n\n$$\\frac{a}{b}$$\n\nend')).toBe('Rate:\n\n$$\n\\frac{a}{b}\n$$\n\nend');
    expect(displayMath('  $$ x + 1 $$  ')).toBe('  $$\n  x + 1\n  $$');
  });
  it('leaves inline $$ and fenced blocks alone', () => {
    expect(displayMath('so $$x$$ inline')).toBe('so $$x$$ inline');
    expect(displayMath('$$\nx\n$$')).toBe('$$\nx\n$$');
    expect(displayMath('$$$$')).toBe('$$$$');
  });
  it('renders a one-line equation as a KaTeX display block', async () => {
    const { container } = render(<Markdown text={'Denial rate:\n\n$$\\frac{denied}{total} = 0.12$$\n\nDone.'} />);
    await waitFor(() => expect(container.querySelector('.katex-display .katex')).not.toBeNull(), { timeout: 4000 });
    expect(container.querySelector('p .katex')).toBeNull();
  });
  it('keeps currency as text next to display math', async () => {
    const { container } = render(<Markdown text={'Paid $412 total.\n\n$$a=b$$'} />);
    await waitFor(() => expect(container.querySelector('.katex-display')).not.toBeNull(), { timeout: 4000 });
    expect(container.textContent).toContain('$412');
  });
});
