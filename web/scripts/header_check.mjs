#!/usr/bin/env node
// Response-header check against a running `vite preview` (and optionally the API). QA and CI run it.
//   node scripts/header_check.mjs [base=http://127.0.0.1:5231/ | none] [--api http://127.0.0.1:8130] [--json]
// base "none" checks only the API (so one server at a time is enough on the shared box).
// Checks CSP (nonce per response, no unsafe-*, frame-ancestors/object-src/base-uri 'none'), no-store on HTML and API,
// nosniff, Referrer-Policy no-referrer, X-Frame-Options DENY, Permissions-Policy. Rules: scripts/header_rules.mjs.
// Exits 1 on any problem. Synthetic requests only; no data is sent.
import { checkHeaders, checkHtmlNonce, cspNonce } from './header_rules.mjs';

const args = process.argv.slice(2);
const flag = (n) => { const i = args.indexOf(n); return i >= 0 ? args.splice(i, 2)[1] : undefined; };
const json = args.includes('--json') && args.splice(args.indexOf('--json'), 1);
const api = flag('--api');
const base = args[0] === 'none' ? null : new URL(args[0] ?? 'http://127.0.0.1:5231/');

const results = [];
const headersOf = (r) => Object.fromEntries(r.headers.entries());
async function probe(name, kind, url, init) {
  try {
    const r = await fetch(url, { redirect: 'manual', ...init });
    const h = headersOf(r);
    const body = init?.method === 'HEAD' ? '' : await r.text();
    const problems = checkHeaders(kind, h);
    results.push({ name, kind, url: String(url), status: r.status, problems });
    return { h, body, status: r.status };
  } catch (e) {
    results.push({ name, kind, url: String(url), status: 0, problems: [`request failed: ${e.cause?.code ?? e.message}`] });
    return null;
  }
}

const root = base && await probe('index', 'html', new URL('/', base));
if (root) {
  const nonceProblems = checkHtmlNonce(root.body, root.h['content-security-policy']);
  const again = await probe('index (2nd request)', 'html', new URL('/', base));
  if (again && cspNonce(again.h['content-security-policy']) === cspNonce(root.h['content-security-policy']))
    nonceProblems.push('CSP nonce is reused across responses');
  results.push({ name: 'nonce in HTML', kind: 'html', url: String(base), status: root.status, problems: nonceProblems });
  const deep = await probe('SPA deep route', 'html', new URL('/c/some-conversation', base));
  if (deep) results.at(-1).problems.push(...checkHtmlNonce(deep.body, deep.h['content-security-policy']));
  await probe('index HEAD', 'html', new URL('/', base), { method: 'HEAD' });
  const asset = /(?:src|href)="(\/assets\/[^"]+\.js)"/.exec(root.body)?.[1];
  if (asset) await probe('hashed JS asset', 'asset', new URL(asset, base));
  else results.push({ name: 'hashed JS asset', kind: 'asset', url: '', status: 0, problems: ['no /assets/*.js in index.html'] });
  const missing = await probe('missing asset (404)', 'asset', new URL('/assets/does-not-exist.js', base));
  if (missing && (missing.status !== 404 || /text\/html/.test(missing.h['content-type'] ?? '')))
    results.at(-1).problems.push(`a missing asset must 404 (not the SPA's index.html); got ${missing.status} ${missing.h['content-type'] ?? ''}`);
}
if (api) {
  await probe('API health', 'api', new URL('/api/health', api));
  await probe('API 404', 'api', new URL('/api/does-not-exist', api));
  await probe('API 401', 'api', new URL('/api/conversations', api));
}

const failed = results.filter((r) => r.problems.length);
if (json) console.log(JSON.stringify({ base: base && String(base), api: api ?? null, results }, null, 2));
else for (const r of results) {
  console.log(`${r.problems.length ? 'FAIL' : 'ok  '} ${r.name} [${r.status}] ${r.url}`);
  for (const p of r.problems) console.log(`       - ${p}`);
}
if (failed.length) { console.error(`header check: ${failed.length} failing response(s)`); process.exit(1); }
