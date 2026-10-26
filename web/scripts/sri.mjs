// Subresource Integrity for built assets (platform, fe-platform-36). Used by vite.config.ts (build only) and by
// scripts/sri_check.mjs (local gate). Pure functions: the caller supplies the bytes of each /assets/ file.
//
// 1. Every <script src="/assets/…"> and <link rel="modulepreload|stylesheet" href="/assets/…"> in index.html gets
//    integrity="sha384-…" (and crossorigin, which it already has from Vite).
// 2. Lazy chunks (vega, shiki, …) are loaded with import(), which has no integrity attribute. An import map with an
//    "integrity" section (HTML spec, Chrome 127+ / Safari 18+; other browsers ignore the key) covers every JS file in
//    /assets/, so a tampered lazy chunk fails to load too. The map carries the same CSP nonce as the other scripts.
import { createHash } from 'node:crypto';

export const sriHash = (bytes) => `sha384-${createHash('sha384').update(bytes).digest('base64')}`;

const TAG = /<(script|link)\b[^>]*>/g;
const ref = (tag) => /\s(?:src|href)="(\/assets\/[^"?#]+)"/.exec(tag)?.[1];
const needsSri = (tag) => /^<script\b/.test(tag) ? /\stype="module"/.test(tag) : /\srel="(?:modulepreload|stylesheet)"/.test(tag);

/** @param {string} html @param {(path: string) => Buffer | Uint8Array | string | undefined} read path like /assets/x.js */
export function applySri(html, read, jsFiles = []) {
  const missing = [];
  let out = html.replace(TAG, (tag) => {
    const p = ref(tag);
    if (!p || !needsSri(tag)) return tag;
    const bytes = read(p);
    if (bytes === undefined) { missing.push(p); return tag; }
    const t = tag.replace(/\sintegrity="[^"]*"/, '');
    const withCors = /\scrossorigin\b/.test(t) ? t : t.replace(/^<(script|link)/, '<$1 crossorigin');
    return withCors.replace(/\s*\/?>$/, (end) => ` integrity="${sriHash(bytes)}"${end}`);
  });
  if (missing.length) throw new Error(`SRI: built file(s) not found: ${missing.join(', ')}`);
  out = out.replace(/\s*<script type="importmap"[^>]*>[\s\S]*?<\/script>/, '');
  const integrity = {};
  for (const p of [...jsFiles].sort()) {
    const bytes = read(p);
    if (bytes === undefined) throw new Error(`SRI: built file not found: ${p}`);
    integrity[p] = sriHash(bytes);
  }
  if (Object.keys(integrity).length) {
    const nonce = /<script[^>]*\snonce="([^"]+)"/.exec(out)?.[1];
    const map = `<script type="importmap"${nonce ? ` nonce="${nonce}"` : ''}>${JSON.stringify({ integrity }).replace(/</g, '\\u003c')}</script>`;
    // An import map must precede every module script and modulepreload.
    const first = out.search(/<script\b[^>]*\stype="module"|<link\b[^>]*\srel="modulepreload"/);
    if (first < 0) throw new Error('SRI: no module script to anchor the import map');
    out = `${out.slice(0, first)}${map}\n    ${out.slice(first)}`;
  }
  return out;
}

/** Problems with SRI in built HTML. Empty when every asset tag and every JS file is covered and the hashes match. */
export function checkSri(html, read, jsFiles = []) {
  const problems = [];
  for (const [tag] of html.matchAll(TAG)) {
    const p = ref(tag);
    if (!p || !needsSri(tag)) continue;
    const got = /\sintegrity="([^"]+)"/.exec(tag)?.[1];
    const bytes = read(p);
    if (!got) problems.push(`${p}: no integrity attribute`);
    else if (bytes === undefined) problems.push(`${p}: file missing`);
    else if (got !== sriHash(bytes)) problems.push(`${p}: integrity mismatch`);
    if (!/\scrossorigin\b/.test(tag)) problems.push(`${p}: no crossorigin attribute`);
  }
  const m = /<script type="importmap"[^>]*>([\s\S]*?)<\/script>/.exec(html);
  if (jsFiles.length) {
    if (!m) problems.push('no import map with integrity');
    else {
      const integrity = JSON.parse(m[1]).integrity ?? {};
      for (const p of jsFiles) {
        const bytes = read(p);
        if (!integrity[p]) problems.push(`${p}: not in import map integrity`);
        else if (bytes !== undefined && integrity[p] !== sriHash(bytes)) problems.push(`${p}: import map integrity mismatch`);
      }
      const firstModule = html.search(/<script\b[^>]*\stype="module"|<link\b[^>]*\srel="modulepreload"/);
      if (firstModule >= 0 && m.index > firstModule) problems.push('import map comes after a module script');
      if (!/\snonce="/.test(m[0])) problems.push('import map has no nonce');
    }
  }
  return problems;
}
