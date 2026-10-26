/**
 * Production-build performance probe. Serve `vite preview` and run:
 *   node scripts/perf_audit.mjs http://127.0.0.1:4173
 * The page must be the chat app. Scenarios use ?audit=1 (in-memory seed, no network).
 */
import { chromium } from 'playwright';
import fs from 'node:fs';

const base = (process.argv[2] || 'http://127.0.0.1:4173').replace(/\/$/, '');
const out = process.env.PERF_OUT || '/tmp/perf-report.json';

async function readStats(page) {
  return page.evaluate(() => {
    const s = window.__ccRenderStats || { commits: 0, actual: 0, worst: 0, children: 0 };
    const mem = performance.memory;
    return {
      commits: s.commits,
      reactMs: Math.round(s.actual * 10) / 10,
      worstCommitMs: Math.round(s.worst * 10) / 10,
      childRenders: s.children,
      nodes: document.getElementsByTagName('*').length,
      messages: document.querySelectorAll('[data-testid="msg-user"], [data-testid="msg-assistant"]').length,
      tableRows: document.querySelectorAll('[data-testid="data-table"] tbody tr').length,
      heapMb: mem ? Math.round(mem.usedJSHeapSize / 1048576 * 10) / 10 : null,
    };
  });
}

async function resetStats(page) {
  await page.evaluate(() => { window.__ccRenderStats = { commits: 0, actual: 0, worst: 0, children: 0 }; });
}

async function gc(page) {
  await page.evaluate(() => { window.gc?.(); });
}

/** Frame gaps and long tasks while `fn` runs. */
async function observe(page, fn) {
  await page.evaluate(() => {
    window.__frames = [];
    window.__long = [];
    let last = performance.now();
    const loop = (t) => {
      window.__frames.push(t - last);
      last = t;
      window.__raf = requestAnimationFrame(loop);
    };
    window.__raf = requestAnimationFrame(loop);
    window.__po = new PerformanceObserver((list) => {
      for (const e of list.getEntries()) window.__long.push(Math.round(e.duration));
    });
    try { window.__po.observe({ entryTypes: ['longtask'] }); } catch { /* not supported */ }
  });
  await fn();
  return page.evaluate(() => {
    cancelAnimationFrame(window.__raf);
    window.__po?.disconnect();
    const frames = (window.__frames || []).slice(1).filter((n) => n > 0);
    const sorted = [...frames].sort((a, b) => a - b);
    const at = (p) => (sorted.length ? sorted[Math.min(sorted.length - 1, Math.floor((p / 100) * (sorted.length - 1)))] : 0);
    const long = window.__long || [];
    return {
      frames: frames.length,
      frameP50Ms: Math.round(at(50) * 10) / 10,
      frameP95Ms: Math.round(at(95) * 10) / 10,
      frameMaxMs: Math.round((sorted.at(-1) || 0) * 10) / 10,
      framesOver16ms: frames.filter((f) => f > 16.7).length,
      framesOver33ms: frames.filter((f) => f > 33.4).length,
      longTasks: long.length,
      longTaskMaxMs: long.length ? Math.max(...long) : 0,
      longTaskTotalMs: long.reduce((a, b) => a + b, 0),
    };
  });
}

async function scrollElement(page, selector, ms) {
  await page.evaluate(async ({ selector, ms }) => {
    const el = document.querySelector(selector);
    if (!el) throw new Error(`missing scroller ${selector}`);
    const start = el.scrollTop;
    const distance = Math.max(0, el.scrollHeight - el.clientHeight - start);
    const t0 = performance.now();
    await new Promise((resolve) => {
      const step = (t) => {
        const p = Math.min(1, (t - t0) / ms);
        el.scrollTop = start + distance * p;
        if (p < 1) requestAnimationFrame(step);
        else resolve();
      };
      requestAnimationFrame(step);
    });
  }, { selector, ms });
}

async function main() {
  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || '/usr/local/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--js-flags=--expose-gc'],
  });
  const page = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 240)));
  await page.goto(`${base}/?audit=1`, { waitUntil: 'networkidle' });
  await page.getByRole('heading', { name: 'What should we look into?' }).waitFor();
  await page.waitForFunction(() => !!window.__ccAudit);
  await gc(page);
  const heapIdle = await page.evaluate(() => Math.round((performance.memory?.usedJSHeapSize || 0) / 1048576 * 10) / 10);

  await resetStats(page);
  const threadFrames = await observe(page, async () => {
    await page.evaluate(() => window.__ccAudit.seedMessages(500));
    await page.waitForSelector('[data-testid="msg-assistant"]');
    await page.waitForTimeout(700);
  });
  const thread = { ...(await readStats(page)), ...threadFrames };

  await resetStats(page);
  const scrollFrames = await observe(page, () => scrollElement(page, 'main .overflow-y-auto', 1200));
  const threadScroll = { ...(await readStats(page)), ...scrollFrames };

  await page.evaluate(() => {
    const el = document.querySelector('main .overflow-y-auto');
    if (el) el.scrollTop = 0;
  });
  await page.waitForTimeout(50);
  await resetStats(page);
  const walkFrames = await observe(page, () => scrollElement(page, 'main .overflow-y-auto', 1200));
  const threadScrollWalk = { ...(await readStats(page)), ...walkFrames };

  await resetStats(page);
  const streamFrames = await observe(page, () => page.evaluate(() => window.__ccAudit.burst(240, 8)));
  const streaming = { ...(await readStats(page)), ...streamFrames };

  await gc(page);
  const heapBeforeTable = await page.evaluate(() => Math.round((performance.memory?.usedJSHeapSize || 0) / 1048576 * 10) / 10);
  await resetStats(page);
  const tableFrames = await observe(page, async () => {
    await page.evaluate(() => window.__ccAudit.seedTable(10000));
    await page.waitForSelector('[data-testid="data-table"]');
    await page.waitForTimeout(400);
  });
  const table = { ...(await readStats(page)), ...tableFrames, heapBeforeMb: heapBeforeTable };
  await gc(page);
  table.heapAfterGcMb = await page.evaluate(() => Math.round((performance.memory?.usedJSHeapSize || 0) / 1048576 * 10) / 10);

  await resetStats(page);
  const tableScrollFrames = await observe(page, () => scrollElement(page, '[data-testid="data-table"] .overflow-auto', 1000));
  const tableScroll = { ...(await readStats(page)), ...tableScrollFrames };

  await resetStats(page);
  const sortFrames = await observe(page, async () => {
    await page.getByRole('button', { name: 'Claims' }).click();
    await page.waitForTimeout(200);
  });
  const tableSort = { ...(await readStats(page)), ...sortFrames };

  await browser.close();
  const report = { base, heapIdleMb: heapIdle, thread, threadScroll, threadScrollWalk, streaming, table, tableScroll, tableSort, errors };
  fs.writeFileSync(out, JSON.stringify(report, null, 2));
  const line = (name, s) => `${name}: commits ${s.commits} children ${s.childRenders} react ${s.reactMs}ms worst ${s.worstCommitMs}ms nodes ${s.nodes} msgs ${s.messages} rows ${s.tableRows} heap ${s.heapMb}MB frames p50/p95/max ${s.frameP50Ms}/${s.frameP95Ms}/${s.frameMaxMs} over16 ${s.framesOver16ms}/${s.frames} long ${s.longTasks} (max ${s.longTaskMaxMs}ms)`;
  console.log(line('thread500', thread));
  console.log(line('scroll500', threadScroll));
  console.log(line('scroll500Walk', threadScrollWalk));
  console.log(line('stream240', streaming));
  console.log(line('table10k', table));
  console.log(line('tableScroll', tableScroll));
  console.log(line('tableSort', tableSort));
  console.log('idle heap', heapIdle, 'errors', errors);
  console.log('wrote', out);
}

main().catch((e) => { console.error(e); process.exit(1); });
