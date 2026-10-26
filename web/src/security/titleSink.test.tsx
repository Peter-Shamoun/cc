/**
 * document.title is a PHI sink: browsers store titles in history, sync them and show them in tab strips during screen
 * shares. No source file may assign
 * document.title from data (the shared view that once showed chat titles was removed with sharing, 12:15 AM).
 */
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup } from '@testing-library/react';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join } from 'node:path';

afterEach(() => { cleanup(); document.title = 'ClaimClarity'; });

describe('document.title never carries chat content', () => {
  it('no source file assigns document.title or names a performance mark from a template or variable', () => {
    const hits: string[] = [];
    const walk = (d: string) => {
      for (const f of readdirSync(d)) {
        const p = join(d, f);
        if (statSync(p).isDirectory()) { walk(p); continue; }
        if (!/\.(ts|tsx)$/.test(f) || /\.test\.tsx?$/.test(f)) continue;
        for (const m of readFileSync(p, 'utf8').matchAll(/document\.title\s*=\s*([^;\n]+)/g)) {
          if (!/^'[^'$]*'$/.test(m[1].trim())) hits.push(`${p}: ${m[0]}`);
        }
        // Performance marks/measures stay in the timeline (and in DevTools/RUM exports): literal names only.
        for (const m of readFileSync(p, 'utf8').matchAll(/performance\.(?:mark|measure)\(\s*([^,)]+)/g)) {
          if (!/^'[^'$]*'$/.test(m[1].trim())) hits.push(`${p}: ${m[0]}`);
        }
      }
    };
    walk(join(__dirname, '..'));
    expect(hits).toEqual([]);
  });
});
