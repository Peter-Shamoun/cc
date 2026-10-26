import { describe, expect, it } from 'vitest';
import { render, waitFor } from '@testing-library/react';
import { Markdown, mayContainMath } from './Markdown';

describe('KaTeX math', () => {
  it('renders $$ math and leaves currency alone', async () => {
    const { container } = render(<Markdown text={'Rate is $$\\frac{denied}{claims}$$ and costs $412 and $380.'} />);
    // KaTeX is lazy-loaded on first math (kept out of the initial bundle).
    await waitFor(() => expect(container.querySelector('.katex')).not.toBeNull());
    expect(container.textContent).toContain('$412 and $380');
  });
  it('renders display math blocks', async () => {
    const { container } = render(<Markdown text={'$$\nE = \\sum_i x_i\n$$'} />);
    await waitFor(() => expect(container.querySelector('.katex-display, .katex')).not.toBeNull());
  });
  it('only loads KaTeX when the text can contain math (currency never triggers it)', () => {
    expect(mayContainMath('Denied \\$412 of \\$1,200')).toBe(false);
    expect(mayContainMath('No math here.')).toBe(false);
    expect(mayContainMath('$E = mc^2$')).toBe(true);
    expect(mayContainMath('\\(x\\)')).toBe(true);
  });
});
