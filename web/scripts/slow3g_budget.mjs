/**
 * Slow-3G first-paint budget for the production (mock) build: local gate step (docs/frontend_ci.md "Slow-3G first paint").
 *
 *   node scripts/slow3g_budget.mjs [distDir=dist-mock] [port=5241]
 *
 * Serves distDir itself, gzip-compressed like production, and stops the server on exit, so the gate runs it with no other
 * server up (BRIEF: one server at a time). One cold load per form factor in headless Chrome under DevTools "Slow 3G"
 * (400 kbps, 2 s RTT); mobile also gets 4x CPU. It records FCP and when the composer ("Message" textbox) becomes visible,
 * then checks them against BUDGET. QA's e2e/review/slow3g.mjs is the screenshot-strip review tool and stays separate.
 * Mock build only: synthetic data, no network beyond 127.0.0.1.
 */
import http from 'node:http';
import fs from 'node:fs';
import path from 'node:path';
import zlib from 'node:zlib';
import { fileURLToPath } from 'node:url';

/** Milliseconds from navigation start, max. Measured 2026-10-25 (sota@03c4268): FCP 2.09 s desktop / 2.09-2.12 s mobile,
 * composer 2.12 / 2.17-2.20 s (QA-P8: FCP was 10.1 s before the boot shell). Tighten, don't loosen. */
export const BUDGET = {
  desktop: { fcp: 2500, composer: 3000 },
  mobile: { fcp: 2500, composer: 3500 },
};

/** Pure: report {desktop:{fcp,composer}, mobile:{...}} -> list of failures (a missing metric fails). */
export function evaluate(report, budget = BUDGET) {
  const errors = [];
  for (const [form, limits] of Object.entries(budget)) {
    for (const [metric, max] of Object.entries(limits)) {
      const v = report?.[form]?.[metric];
      if (typeof v !== 'number' || !Number.isFinite(v)) errors.push(`${form} ${metric}: not measured`);
      else if (v > max) errors.push(`${form} ${metric}: ${v} ms > ${max} ms`);
    }
  }
  return errors;
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.json': 'application/json' };

function serve(root, port) {
  const srv = http.createServer((req, res) => {
    let f = path.join(root, decodeURIComponent(new URL(req.url, 'http://x').pathname));
    if (!f.startsWith(root + path.sep) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(root, 'index.html');
    const ext = path.extname(f);
    let body = fs.readFileSync(f);
    const h = { 'content-type': TYPES[ext] || 'application/octet-stream', 'cache-control': 'no-store' };
    if (['.html', '.js', '.css', '.json', '.svg'].includes(ext)) { body = zlib.gzipSync(body, { level: 9 }); h['content-encoding'] = 'gzip'; }
    res.writeHead(200, h);
    res.end(body);
  });
  return new Promise((ok, fail) => { srv.once('error', fail); srv.listen(port, '127.0.0.1', () => ok(srv)); });
}

async function measure(browser, url, form) {
  const mobile = form === 'mobile';
  const ctx = await browser.newContext({ viewport: mobile ? { width: 390, height: 844 } : { width: 1440, height: 900 }, isMobile: mobile, hasTouch: mobile });
  try {
    const p = await ctx.newPage();
    const cdp = await ctx.newCDPSession(p);
    await cdp.send('Network.enable');
    await cdp.send('Network.setCacheDisabled', { cacheDisabled: true });
    await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 2000, downloadThroughput: (400 * 1024) / 8, uploadThroughput: (400 * 1024) / 8 });
    await cdp.send('Emulation.setCPUThrottlingRate', { rate: mobile ? 4 : 1 });
    // Paint entries can land after the composer check, so collect them from the start.
    await p.addInitScript(() => {
      new PerformanceObserver((l) => { for (const e of l.getEntries()) if (e.name === 'first-contentful-paint') window.__s3gFcp = e.startTime; }).observe({ type: 'paint', buffered: true });
    });
    await p.goto(url, { waitUntil: 'commit', timeout: 60000 });
    await p.getByRole('textbox', { name: 'Message' }).waitFor({ state: 'visible', timeout: 30000 }).catch(() => {});
    await p.waitForFunction(() => window.__s3gFcp !== undefined, null, { timeout: 10000 }).catch(() => {});
    const composerAt = await p.evaluate(() => Math.round(performance.now()));
    return await p.evaluate((composerAt) => {
      const fcp = window.__s3gFcp;
      const composer = document.querySelector('[aria-label="Message"], textarea');
      const visible = composer && composer.getBoundingClientRect().height > 0;
      return { fcp: typeof fcp === 'number' ? Math.round(fcp) : null, composer: visible ? composerAt : null };
    }, composerAt);
  } finally {
    await ctx.close();
  }
}

async function main() {
  const root = path.resolve(process.argv[2] || 'dist-mock');
  const port = Number(process.argv[3] || 5241);
  if (!fs.existsSync(path.join(root, 'index.html'))) { console.error(`no build at ${root}`); process.exit(2); }
  const { chromium } = await import('playwright');
  const srv = await serve(root, port);
  const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined) });
  const report = {};
  try {
    for (const form of ['desktop', 'mobile']) report[form] = await measure(browser, `http://127.0.0.1:${port}/`, form);
  } finally {
    await browser.close();
    srv.close();
  }
  for (const [form, r] of Object.entries(report)) console.log(`${form.padEnd(8)} FCP ${r.fcp} ms (budget ${BUDGET[form].fcp}), composer ${r.composer} ms (budget ${BUDGET[form].composer})`);
  const errors = evaluate(report);
  errors.forEach((e) => console.error(`over budget: ${e}`));
  process.exit(errors.length ? 1 : 0);
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await main();
