import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

/** QA C9 fidelity findings S28/S30/S31 (docs/frontend-fidelity-audit.md F6/F11/F12), held in place at source level. */
const src = resolve(process.cwd(), 'src');
const walk = (d: string): string[] => readdirSync(d).flatMap((f) => {
  const p = join(d, f);
  return statSync(p).isDirectory() ? walk(p) : /\.tsx?$/.test(f) && !f.includes('.test.') ? [p] : [];
});
const files = walk(src).filter((f) => !f.includes(`${'/'}dev${'/'}`));
const css = readFileSync(join(src, 'index.css'), 'utf8');

describe('fidelity (QA C9)', () => {
  it('S28: menus and dialogs use the soft shadow tokens, nothing past 32px blur or .12 alpha', () => {
    expect(css).toMatch(/--shadow-menu:\s*0 4px 24px rgba\(0,0,0,0\.10\)/);
    expect(css).toMatch(/--shadow-dialog:\s*0 8px 32px rgba\(0,0,0,0\.12\)/);
    const heavy: string[] = [];
    for (const f of files) {
      for (const m of readFileSync(f, 'utf8').matchAll(/shadow-\[0_\d+px_(\d+)px_rgba\(0,0,0,(0?\.\d+)\)\]/g)) {
        if (Number(m[1]) > 32 || Number(m[2]) > 0.12) heavy.push(`${f.replace(src, '')}: ${m[0]}`);
      }
    }
    expect(heavy).toEqual([]);
  });
  it('S30: one lighter icon stroke everywhere, no heavy 2.5/3 strokes', () => {
    expect(css).toMatch(/svg\.lucide\s*\{\s*stroke-width:\s*1\.75;/);
    const heavy = files.filter((f) => /strokeWidth=\{(2\.[5-9]|3)\}/.test(readFileSync(f, 'utf8')));
    expect(heavy).toEqual([]);
  });
  it('S31: the thread, docked composer and shared page give content a 48rem (768px) column after padding', () => {
    for (const f of ['shell/AppShell.tsx', 'shell/SharedView.tsx']) {
      const s = readFileSync(join(src, f), 'utf8');
      // 51rem minus md:px-6 (2 x 1.5rem) = 48rem
      expect(s).not.toMatch(/max-w-\[48rem\][^"]*md:px-6/);
      expect(s).toMatch(/max-w-\[51rem\][^"]*md:px-6/);
    }
  });
});
