#!/usr/bin/env node
// Licence audit for web/ from package-lock.json, so it needs no install and no network.
//   node scripts/license_audit.mjs            summary; exits 1 if a shipped (non-dev) package is not permissive
//   node scripts/license_audit.mjs --markdown full table, used for docs/frontend_licenses.md
import fs from 'node:fs';

export const PERMISSIVE = new Set([
  'MIT', 'MIT-0', 'ISC', 'BSD-2-Clause', 'BSD-3-Clause', '0BSD', 'Apache-2.0', 'Unlicense', 'CC0-1.0',
  'BlueOak-1.0.0', 'Python-2.0', 'Zlib', 'CC-BY-4.0',
]);
// Weak copyleft: fine for build/test tooling that is never shipped; flagged if it reaches the bundle.
export const WEAK_COPYLEFT = new Set(['MPL-2.0', 'LGPL-2.1', 'LGPL-3.0', 'EPL-2.0']);

/** Split an SPDX expression into its ids. "A OR B" passes if any option is permissive; "A AND B" needs all. */
export function classify(expr) {
  const e = String(expr ?? '').replace(/[()]/g, '').trim();
  if (!e) return 'unknown';
  const any = (ids) => ids.map((s) => s.trim());
  if (/ OR /.test(e)) {
    const c = any(e.split(/ OR /)).map(classify);
    return c.includes('permissive') ? 'permissive' : c.includes('weak-copyleft') ? 'weak-copyleft' : c[0];
  }
  if (/ AND /.test(e)) {
    const c = any(e.split(/ AND /)).map(classify);
    return c.every((x) => x === 'permissive') ? 'permissive' : c.find((x) => x !== 'permissive');
  }
  if (PERMISSIVE.has(e)) return 'permissive';
  if (WEAK_COPYLEFT.has(e)) return 'weak-copyleft';
  if (/GPL|AGPL|SSPL|BUSL|Commons-Clause|CC-BY-NC/i.test(e)) return 'copyleft-or-restricted';
  return 'unknown';
}

export function audit(lock) {
  const rows = [];
  for (const [key, p] of Object.entries(lock.packages ?? {})) {
    if (!key || p.link) continue;
    const name = key.replace(/^.*node_modules\//, '');
    rows.push({ name, version: p.version, license: p.license ?? '', dev: !!p.dev, optional: !!p.optional, cls: classify(p.license) });
  }
  rows.sort((a, b) => a.name.localeCompare(b.name) || String(a.version).localeCompare(String(b.version)));
  const problems = rows.filter((r) => !r.dev && r.cls !== 'permissive').map((r) => `${r.name}@${r.version}: ${r.license || 'no licence'} (${r.cls}) ships in the app`);
  const warnings = rows.filter((r) => r.dev && r.cls !== 'permissive').map((r) => `${r.name}@${r.version}: ${r.license || 'no licence'} (${r.cls}, dev/build only)`);
  return { rows, problems, warnings };
}

if (import.meta.url === `file://${process.argv[1]}`) {
  const lock = JSON.parse(fs.readFileSync(new URL('../package-lock.json', import.meta.url), 'utf8'));
  const { rows, problems, warnings } = audit(lock);
  if (process.argv.includes('--markdown')) {
    console.log('| Package | Version | Licence | Scope |\n|---|---|---|---|');
    for (const r of rows) console.log(`| ${r.name} | ${r.version} | ${r.license} | ${r.dev ? 'dev' : 'runtime'}${r.optional ? ' (optional)' : ''} |`);
  } else {
    const count = (f) => { const m = {}; for (const r of rows.filter(f)) m[r.license] = (m[r.license] ?? 0) + 1; return m; };
    console.log(JSON.stringify({ packages: rows.length, runtime: count((r) => !r.dev), dev: count((r) => r.dev), problems, warnings }, null, 2));
  }
  process.exit(problems.length ? 1 : 0);
}
