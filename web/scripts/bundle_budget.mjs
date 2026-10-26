#!/usr/bin/env node
// Bundle-size budget for web/dist (run after `npm run build`). Budgets live in bundle-budget.json (gzipped KB).
// "Initial" = what index.html loads before first paint: its entry <script> plus every modulepreload and stylesheet.
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';

const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), '..');
const dist = path.join(root, process.argv[2] ?? 'dist');
const budget = JSON.parse(fs.readFileSync(path.join(root, 'bundle-budget.json'), 'utf8'));
const html = fs.readFileSync(path.join(dist, 'index.html'), 'utf8');
const initial = new Set([...html.matchAll(/(?:src|href)="\/(assets\/[^"]+\.(?:js|css))"/g)].map((m) => m[1]));
const kb = (n) => Math.round((n / 1024) * 10) / 10;

const assets = fs.readdirSync(path.join(dist, 'assets')).map((f) => {
  const buf = fs.readFileSync(path.join(dist, 'assets', f));
  return { file: `assets/${f}`, raw: buf.length, gz: /\.(js|css|svg|json|html)$/.test(f) ? zlib.gzipSync(buf, { level: 9 }).length : buf.length };
});
const js = assets.filter((a) => a.file.endsWith('.js'));
const sum = (xs) => xs.reduce((n, a) => n + a.gz, 0);
const initialJs = sum(js.filter((a) => initial.has(a.file)));
const initialCss = sum(assets.filter((a) => a.file.endsWith('.css') && initial.has(a.file)));
const lazy = js.filter((a) => !initial.has(a.file)).sort((a, b) => b.gz - a.gz);

const checks = [
  ['initial JS (gz KB)', kb(initialJs), budget.initialJsGzKb],
  ['initial CSS (gz KB)', kb(initialCss), budget.initialCssGzKb],
  [`largest lazy chunk (gz KB) ${lazy[0]?.file ?? ''}`, kb(lazy[0]?.gz ?? 0), budget.maxLazyChunkGzKb],
  ['total JS (gz KB)', kb(sum(js)), budget.totalJsGzKb],
  ['asset count', assets.length, budget.maxAssetCount],
];
let failed = 0;
for (const [name, value, max] of checks) {
  const ok = value <= max;
  if (!ok) failed++;
  console.log(`${ok ? 'ok  ' : 'FAIL'} ${name}: ${value} / ${max}`);
}
console.log('largest lazy chunks:', lazy.slice(0, 5).map((a) => `${a.file} ${kb(a.gz)}`).join(', '));
if (failed) { console.error(`bundle budget exceeded (${failed}); see web/bundle-budget.json`); process.exit(1); }
