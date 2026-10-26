/**
 * Guards the print rules the e2e print spec exercises in Chrome (e2e/print.spec.ts), so the vitest gate catches a
 * deleted rule too. jsdom has no print layout, so this checks the stylesheet text.
 */
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

const css = readFileSync(resolve(__dirname, 'index.css'), 'utf8');
const print = (() => { const i = css.indexOf('@media print {'); let d = 0; for (let j = i; j < css.length; j++) { if (css[j] === '{') d++; else if (css[j] === '}' && --d === 0) return css.slice(i, j + 1); } return ''; })();

describe('print stylesheet rules', () => {
  it('scales Vega charts and images to the paper column', () => {
    expect(print).toMatch(/\[data-testid="chart"\] svg\.marks[^{]*\{[^}]*max-width: 100% !important;[^}]*height: auto !important/);
  });
  it('wraps code, un-sticks table headers and repeats them per page', () => {
    expect(print).toMatch(/\.prose-chat pre[^{]*\{[^}]*white-space: pre-wrap !important/);
    expect(print).toMatch(/thead \{[^}]*position: static !important;[^}]*table-header-group/);
  });
  it('is flat and hides screen-only controls', () => {
    expect(print).toMatch(/box-shadow: none !important/);
    expect(print).toMatch(/\[data-print-hide\][^{]*\{ display: none !important/);
  });
});
