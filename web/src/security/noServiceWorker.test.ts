import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * Decision (docs/frontend_security.md "Caching and offline"): no service worker, no Cache Storage, no IndexedDB,
 * no web manifest. A worker that caches responses would put answers, tables and CSVs (PHI) on disk past sign-out,
 * retention and key shredding. This guards the decision; change the doc first if it ever changes.
 */
const root = path.resolve(__dirname, '../..');
const files: string[] = [];
const walk = (d: string) => fs.readdirSync(d, { withFileTypes: true }).forEach((e) => {
  const p = path.join(d, e.name);
  if (e.isDirectory()) walk(p); else if (/\.(ts|tsx|js|mjs)$/.test(e.name) && !/\.test\./.test(e.name)) files.push(p);
});
walk(path.join(root, 'src'));
walk(path.join(root, 'public'));

describe('caching and offline', () => {
  it('registers no service worker and uses no Cache Storage or IndexedDB', () => {
    const hits = files.filter((f) => /navigator\.serviceWorker|\bcaches\.(open|match|keys)|\bindexedDB\b|\bCacheStorage\b/.test(fs.readFileSync(f, 'utf8')));
    expect(hits.map((f) => path.relative(root, f))).toEqual([]);
  });
  it('ships no worker script or web manifest', () => {
    const pub = fs.readdirSync(path.join(root, 'public'));
    expect(pub.filter((n) => /^(sw|service-worker|workbox).*\.js$|manifest\.(json|webmanifest)$/i.test(n))).toEqual([]);
    expect(fs.readFileSync(path.join(root, 'index.html'), 'utf8')).not.toMatch(/rel="manifest"/);
  });
});
