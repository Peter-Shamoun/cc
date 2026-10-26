import { describe, expect, it } from 'vitest';
import { render } from '@testing-library/react';
import { Markdown } from './Markdown';

describe('KaTeX math', () => {
  it('renders $$ math and leaves currency alone', () => {
    const { container } = render(<Markdown text={'Rate is $$\\frac{denied}{claims}$$ and costs $412 and $380.'} />);
    expect(container.querySelector('.katex')).not.toBeNull();
    expect(container.textContent).toContain('$412 and $380');
  });
  it('renders display math blocks', () => {
    const { container } = render(<Markdown text={'$$\nE = \\sum_i x_i\n$$'} />);
    expect(container.querySelector('.katex-display, .katex')).not.toBeNull();
  });
});
