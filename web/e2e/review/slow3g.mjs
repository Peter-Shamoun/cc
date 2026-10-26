// QA first-impression review: fresh load at Slow 3G (DevTools preset: 400 kbps, 2 s RTT) with gzip like production.
// Starts its own gzip static server on E2E_STATIC_PORT (default 5240) for the mock build and stops it at the end.
// Usage: node e2e/review/slow3g.mjs <outDir>
import { chromium } from 'playwright';
import http from 'node:http'; import fs from 'node:fs'; import path from 'node:path'; import zlib from 'node:zlib';
const out = process.argv[2]; fs.mkdirSync(out, { recursive: true });
const root = path.resolve('e2e/.dist/mock'); const port = Number(process.env.E2E_STATIC_PORT || 5240);
const types = { '.html': 'text/html', '.js': 'text/javascript', '.css': 'text/css', '.woff2': 'font/woff2', '.woff': 'font/woff', '.ttf': 'font/ttf', '.svg': 'image/svg+xml', '.json': 'application/json' };
const srv = http.createServer((req, res) => {
  let f = path.join(root, decodeURIComponent(new URL(req.url, 'http://x').pathname));
  if (!f.startsWith(root) || !fs.existsSync(f) || fs.statSync(f).isDirectory()) f = path.join(root, 'index.html');
  const ext = path.extname(f); let body = fs.readFileSync(f);
  const h = { 'content-type': types[ext] || 'application/octet-stream', 'cache-control': 'no-store' };
  if (['.html', '.js', '.css', '.json', '.svg'].includes(ext)) { body = zlib.gzipSync(body, { level: 9 }); h['content-encoding'] = 'gzip'; }
  res.writeHead(200, h); res.end(body);
}).listen(port, '127.0.0.1');
const url = `http://127.0.0.1:${port}/`;
const b = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined });
const report = {};
for (const [name, vp, cpu] of [['desktop', { width: 1440, height: 900 }, 1], ['mobile', { width: 390, height: 844 }, 4]]) {
  const ctx = await b.newContext({ viewport: vp, deviceScaleFactor: 1, isMobile: name === 'mobile', hasTouch: name === 'mobile' });
  const p = await ctx.newPage();
  const cdp = await ctx.newCDPSession(p);
  await cdp.send('Network.enable');
  await cdp.send('Network.emulateNetworkConditions', { offline: false, latency: 2000, downloadThroughput: 400 * 1024 / 8, uploadThroughput: 400 * 1024 / 8 });
  await cdp.send('Emulation.setCPUThrottlingRate', { rate: cpu });
  let bytes = 0, reqs = 0; cdp.on('Network.loadingFinished', (e) => { bytes += e.encodedDataLength; reqs++; });
  const t0 = Date.now(); const frames = [];
  const nav = p.goto(url, { waitUntil: 'commit', timeout: 120000 });
  const shoot = async () => { const t = Date.now() - t0; const f = `${out}/s3g_${name}_${String(t).padStart(5, '0')}ms.png`; try { await p.screenshot({ path: f, timeout: 5000 }); frames.push(t); } catch {} };
  await nav;
  let composerAt = null;
  for (let i = 0; i < 40 && Date.now() - t0 < 30000; i++) {
    await shoot();
    if (composerAt === null && await p.getByRole('textbox', { name: 'Message' }).isVisible().catch(() => false)) { composerAt = Date.now() - t0; await shoot(); break; }
    await p.waitForTimeout(500);
  }
  await p.waitForLoadState('load', { timeout: 60000 }).catch(() => {});
  const vitals = await p.evaluate(() => new Promise((r) => {
    const o = {}; const fcp = performance.getEntriesByName('first-contentful-paint')[0]; o.fcp = fcp && Math.round(fcp.startTime);
    new PerformanceObserver((l) => { const e = l.getEntries().pop(); o.lcp = Math.round(e.startTime); o.lcpEl = e.element?.tagName + (e.element?.textContent || '').slice(0, 40); }).observe({ type: 'largest-contentful-paint', buffered: true });
    o.load = Math.round(performance.getEntriesByType('navigation')[0]?.loadEventEnd || 0);
    setTimeout(() => r(o), 300);
  }));
  // Typing works right away?
  const tIn = Date.now(); await p.getByRole('textbox', { name: 'Message' }).fill('hello'); const typeMs = Date.now() - tIn;
  report[name] = { composerVisibleMs: composerAt, ...vitals, transferKB: Math.round(bytes / 1024), requests: reqs, firstTypeMs: typeMs, frames };
  await ctx.close();
}
fs.writeFileSync(`${out}/slow3g.json`, JSON.stringify(report, null, 1));
console.log(JSON.stringify(report, null, 1));
await b.close(); srv.close();
