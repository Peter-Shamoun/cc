/**
 * Lighthouse perf / a11y / best-practices budget for the production build (docs/frontend_ci.md "Perf budget").
 *
 *   node scripts/lighthouse_budget.mjs http://127.0.0.1:5231/          # runs Lighthouse (pinned) desktop + mobile
 *   node scripts/lighthouse_budget.mjs --reports a.json b.json         # only checks existing reports (preset from report)
 *
 * Lighthouse is NOT a repo dependency (~114 packages): it runs as `npx -y lighthouse@<pinned>` unless
 * LIGHTHOUSE_BIN points at an install. Chrome comes from CHROME_PATH (else /usr/bin/google-chrome).
 * LH_RUNS (default 3) runs per preset; metrics use the median run, categories the median score.
 * Reports land in LH_OUT (default /tmp/lighthouse) for the CI artifact. The page is the mock build: synthetic data only.
 */
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));

export function median(xs) {
  const s = [...xs].sort((a, b) => a - b);
  const m = s.length >> 1;
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

/** Pure check of one preset's reports against its budget: categories are minimums, metrics maximums. */
export function evaluate(preset, reports, budget) {
  const errors = [];
  const ok = reports.filter((r) => {
    if (r.runtimeError) errors.push(`${preset}: Lighthouse runtime error ${r.runtimeError.code}`);
    return !r.runtimeError;
  });
  if (!ok.length) return { rows: [], errors: errors.length ? errors : [`${preset}: no reports`] };
  const rows = [];
  for (const [key, limit] of Object.entries(budget.categories)) {
    const scores = ok.map((r) => r.categories[key]?.score).filter((v) => typeof v === 'number');
    const value = scores.length ? median(scores) : null;
    rows.push({ preset, key, value, limit, ok: value !== null && value >= limit, kind: 'category' });
  }
  for (const [key, limit] of Object.entries(budget.metrics)) {
    const vals = ok.map((r) => r.audits[key]?.numericValue).filter((v) => typeof v === 'number');
    const value = vals.length ? median(vals) : null;
    rows.push({ preset, key, value, limit, ok: value !== null && value <= limit, kind: 'metric' });
  }
  return { rows, errors };
}

export function formatRows(rows) {
  const fmt = (r, v) => (v === null ? 'n/a' : r.kind === 'category' ? v.toFixed(2) : r.key === 'cumulative-layout-shift' ? v.toFixed(3) : String(Math.round(v)));
  return rows.map((r) => `${r.ok ? 'ok  ' : 'FAIL'} ${r.preset.padEnd(7)} ${r.key.padEnd(26)} ${fmt(r, r.value).padStart(8)} ${r.kind === 'category' ? '>=' : '<='} ${fmt(r, r.limit)}`).join('\n');
}

function runLighthouse(url, preset, outFile, version) {
  const chrome = process.env.CHROME_PATH || (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined);
  const [cmd, pre] = process.env.LIGHTHOUSE_BIN ? [process.env.LIGHTHOUSE_BIN, []] : ['npx', ['-y', `lighthouse@${version}`]];
  const args = [...pre, url, '--only-categories=performance,accessibility,best-practices', '--output=json',
    `--output-path=${outFile}`, '--quiet', '--chrome-flags=--headless=new --no-sandbox --disable-dev-shm-usage'];
  if (preset === 'desktop') args.push('--preset=desktop');
  // Lighthouse has transient trace failures (NO_NAVSTART, NO_FCP on a cold Chrome). Retry those; a persistent
  // runtime error is still returned and fails the budget in evaluate().
  let last = { runtimeError: { code: 'LIGHTHOUSE_DID_NOT_RUN' }, categories: {}, audits: {} };
  for (let attempt = 1; attempt <= 3; attempt++) {
    fs.rmSync(outFile, { force: true });
    try {
      execFileSync(cmd, args, { stdio: ['ignore', 'ignore', 'inherit'], env: { ...process.env, ...(chrome ? { CHROME_PATH: chrome } : {}) } });
    } catch (e) {
      last = { runtimeError: { code: `LIGHTHOUSE_EXIT_${e.status ?? 'ERR'}` }, categories: {}, audits: {} };
    }
    if (fs.existsSync(outFile)) last = JSON.parse(fs.readFileSync(outFile, 'utf8'));
    if (!last.runtimeError) return last;
    console.error(`lighthouse ${preset}: ${last.runtimeError.code} (attempt ${attempt}/3)`);
  }
  return last;
}

function main(argv) {
  const config = JSON.parse(fs.readFileSync(path.join(here, 'perf_budget.json'), 'utf8'));
  const byPreset = {};
  if (argv[0] === '--reports') {
    for (const f of argv.slice(1)) {
      const r = JSON.parse(fs.readFileSync(f, 'utf8'));
      const preset = r.configSettings?.formFactor === 'desktop' ? 'desktop' : 'mobile';
      (byPreset[preset] ??= []).push(r);
    }
  } else {
    const url = argv[0] || 'http://127.0.0.1:4173/';
    const runs = Math.max(1, Number(process.env.LH_RUNS ?? 3));
    const out = process.env.LH_OUT || '/tmp/lighthouse';
    fs.mkdirSync(out, { recursive: true });
    for (const preset of Object.keys(config.presets)) {
      byPreset[preset] = [];
      for (let i = 0; i < runs; i++) byPreset[preset].push(runLighthouse(url, preset, path.join(out, `${preset}-${i + 1}.json`), config.lighthouse));
    }
  }
  let failed = false;
  for (const [preset, reports] of Object.entries(byPreset)) {
    const budget = config.presets[preset];
    if (!budget) continue;
    const { rows, errors } = evaluate(preset, reports, budget);
    console.log(formatRows(rows));
    for (const e of errors) console.log(`FAIL ${e}`);
    failed ||= errors.length > 0 || rows.some((r) => !r.ok);
  }
  if (!Object.keys(byPreset).length) { console.log('FAIL no reports'); failed = true; }
  console.log(failed ? 'Lighthouse budget: FAIL' : 'Lighthouse budget: ok');
  return failed ? 1 : 0;
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) process.exitCode = main(process.argv.slice(2));
