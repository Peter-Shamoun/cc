/**
 * KaTeX math for Markdown, split out of the initial bundle (KaTeX + rehype-katex are ~40% of it).
 * Markdown loads this only when a message contains math syntax; currency like `$412` never triggers it.
 */
import { createMathPlugin } from '@streamdown/math';
import 'katex/dist/katex.min.css';

export const mathPlugin = createMathPlugin({ singleDollarTextMath: true, errorColor: 'var(--danger)' });
