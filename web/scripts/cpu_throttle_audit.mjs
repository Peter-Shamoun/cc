/**
 * Slow-device audit: Chrome CPU throttled 6x (CDP Emulation.setCPUThrottlingRate), against a served build.
 * Measures first paint, layout shift, long tasks and frame gaps while an answer streams, and input latency while
 * typing during the stream. Usage: node scripts/cpu_throttle_audit.mjs http://127.0.0.1:5239/ [rate=6] [out.json]
 */
import { chromium } from 'playwright';
import fs from 'node:fs';
const url = process.argv[2] ?? 'http://127.0.0.1:5239/';
const rate = Number(process.argv[3] ?? 6);
const out = process.argv[4];
const b = await chromium.launch({ executablePath: process.env.CHROME_PATH ?? '/usr/bin/google-chrome' });
const ctx = await b.newContext({ viewport: { width: 1280, height: 900 } });
const pg = await ctx.newPage();
const cdp = await ctx.newCDPSession(pg);
await cdp.send('Emulation.setCPUThrottlingRate', { rate });
await pg.addInitScript(() => {
  const w = window;
  w.__perf = { cls: 0, shifts: [], long: [], events: [], frames: [] };
  new PerformanceObserver((l) => { for (const e of l.getEntries()) if (!e.hadRecentInput) { w.__perf.cls += e.value; w.__perf.shifts.push({ v: +e.value.toFixed(4), t: Math.round(e.startTime), src: (e.sources ?? []).map((s) => s.node?.nodeName + '.' + String(s.node?.className ?? '').slice(0, 50)) }); } }).observe({ type: 'layout-shift', buffered: true });
  new PerformanceObserver((l) => { for (const e of l.getEntries()) w.__perf.long.push({ t: Math.round(e.startTime), d: Math.round(e.duration) }); }).observe({ type: 'longtask', buffered: true });
  new PerformanceObserver((l) => { for (const e of l.getEntries()) w.__perf.events.push({ n: e.name, d: Math.round(e.duration), t: Math.round(e.startTime) }); }).observe({ type: 'event', buffered: true, durationThreshold: 16 });
});
const t0 = Date.now();
await pg.goto(url);
await pg.getByRole('textbox', { name: 'Message' }).waitFor();
const load = await pg.evaluate(() => {
  const p = Object.fromEntries(performance.getEntriesByType('paint').map((e) => [e.name, Math.round(e.startTime)]));
  return { fcp: p['first-contentful-paint'], fp: p['first-paint'], composerReadyMs: Math.round(performance.now()) };
});
const clsLoad = await pg.evaluate(() => window.__perf.cls);
// A person reads the empty state before typing; idle work (chunk warm-up) happens here. THINK_MS=0 for worst case.
await pg.waitForTimeout(Number(process.env.THINK_MS ?? 2000));
// Stream an answer; record rAF gaps and type into the composer while it streams.
await pg.evaluate(() => { const w = window; w.__perf.long = []; w.__perf.events = []; let last = performance.now(); const tick = (t) => { w.__perf.frames.push(t - last); last = t; if (!w.__stopFrames) requestAnimationFrame(tick); }; requestAnimationFrame(tick); });
const box = pg.getByRole('textbox', { name: 'Message' });
await box.fill(process.env.PROMPT ?? 'Plot the monthly denial rate trend for our top 3 payers');
const tSend = Date.now();
await pg.keyboard.press('Enter');
await pg.waitForTimeout(300);
await box.click();
await pg.keyboard.type('follow up typed while streaming', { delay: 60 });
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
const streamMs = Date.now() - tSend;
const r = await pg.evaluate(() => {
  const w = window; w.__stopFrames = true;
  const f = w.__perf.frames.slice(1).sort((a, b) => a - b);
  const pct = (q) => Math.round(f[Math.min(f.length - 1, Math.floor(q * f.length))] ?? 0);
  const keys = w.__perf.events.filter((e) => /key|input|pointer|click/.test(e.n)).map((e) => e.d).sort((a, b) => a - b);
  return {
    cls: +w.__perf.cls.toFixed(4), shifts: w.__perf.shifts.slice(-8),
    longTasks: w.__perf.long.length, longTaskMax: Math.max(0, ...w.__perf.long.map((x) => x.d)), longTaskTotal: w.__perf.long.reduce((s, x) => s + x.d, 0),
    frames: f.length, frameP50: pct(0.5), frameP95: pct(0.95), frameMax: Math.round(f.at(-1) ?? 0), framesOver50: f.filter((x) => x > 50).length,
    inputEvents: keys.length, inputP75: keys[Math.floor(keys.length * 0.75)] ?? 0, inputMax: keys.at(-1) ?? 0,
    typed: document.querySelector('textarea')?.value,
  };
});
const res = { rate, load, clsLoad: +clsLoad.toFixed(4), wallLoadMs: tSend - t0, streamMs, ...r };
console.log(JSON.stringify(res, null, 1));
if (out) fs.writeFileSync(out, JSON.stringify(res, null, 1));
await b.close();
