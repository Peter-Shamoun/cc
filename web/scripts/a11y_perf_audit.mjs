/**
 * Accessibility audit for the chat UI against the demo API.
 *
 * A small proxy holds the SSE stream so axe can scan the analyzing state and
 * the streaming-text state, which otherwise last only a few frames.
 *
 * Usage: node scripts/a11y_perf_audit.mjs [baseUrl]
 * Writes a JSON report path given by AUDIT_OUT (default /tmp/a11y-report.json).
 */
import fs from 'node:fs';
import http from 'node:http';
import { createRequire } from 'node:module';
import { spawn } from 'node:child_process';
import { chromium } from 'playwright';

const require = createRequire(import.meta.url);
const axePath = require.resolve('axe-core/axe.min.js');
const OUT = process.env.AUDIT_OUT || '/tmp/a11y-report.json';
const APP = process.argv[2] || process.env.AUDIT_URL || '';

function delay(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function createHoldProxy(port) {
  /** @type {{ phase: 'forward' | 'hold', holdAfter: string | null, written: string[], queue: string[], wait: (() => void) | null } | null} */
  let active = null;

  const server = http.createServer((req, res) => {
    const url = new URL(req.url || '/', 'http://127.0.0.1');
    if (url.pathname.startsWith('/__audit/')) {
      const cmd = url.pathname.slice('/__audit/'.length);
      let body = '';
      req.on('data', (c) => { body += c; });
      req.on('end', () => {
        if (!active) {
          res.writeHead(409); res.end('no active stream'); return;
        }
        if (cmd === 'state') {
          res.writeHead(200, { 'content-type': 'application/json' });
          res.end(JSON.stringify({ phase: active.phase, holdAfter: active.holdAfter, written: active.written.length, queued: active.queue.length, last: active.written.at(-1) || null }));
          return;
        }
        if (cmd === 'arm') {
          active.holdAfter = url.searchParams.get('after');
          active.phase = 'forward';
          active.wait?.();
          active.wait = null;
        } else if (cmd === 'release') {
          active.holdAfter = null;
          active.phase = 'forward';
          active.wait?.();
          active.wait = null;
        }
        res.writeHead(200); res.end('ok');
      });
      return;
    }

    const headers = { ...req.headers, host: '127.0.0.1:8787' };
    const preq = http.request({ hostname: '127.0.0.1', port: 8787, path: req.url, method: req.method, headers }, (pres) => {
      const isChat = req.method === 'POST' && (req.url || '').startsWith('/api/chat');
      if (!isChat) {
        res.writeHead(pres.statusCode || 500, pres.headers);
        pres.pipe(res);
        return;
      }
      const control = { phase: 'forward', holdAfter: 'analyzing', written: [], queue: [], wait: null };
      active = control;
      res.writeHead(pres.statusCode || 200, pres.headers);
      let buf = '';
      let ended = false;
      pres.on('data', (c) => { buf += c.toString(); pull(); });
      pres.on('end', () => { ended = true; pull(); });
      pres.on('error', () => { ended = true; });

      function pull() {
        let i;
        while ((i = buf.indexOf('\n\n')) >= 0) {
          control.queue.push(buf.slice(0, i + 2));
          buf = buf.slice(i + 2);
        }
        if (ended && buf) { control.queue.push(buf); buf = ''; }
      }

      (async () => {
        while (true) {
          if (!control.queue.length) {
            if (ended) break;
            await delay(8);
            continue;
          }
          if (control.phase === 'hold') {
            await new Promise((r) => { control.wait = r; });
            continue;
          }
          const ev = control.queue.shift();
          res.write(ev);
          const type = /"type":"([^"]+)"/.exec(ev)?.[1] || 'other';
          control.written.push(type);
          const holdAnalyzing = control.holdAfter === 'analyzing' && type === 'status' && ev.includes('Analyzing');
          const holdDelta = control.holdAfter === 'text.delta' && type === 'text.delta';
          if (holdAnalyzing || holdDelta) control.phase = 'hold';
        }
        res.end();
      })().catch((e) => { res.end(); console.error('proxy stream', e); });
    });
    req.pipe(preq);
    preq.on('error', () => { res.writeHead(502); res.end('proxy error'); });
  });

  return new Promise((resolve) => {
    server.listen(port, '127.0.0.1', () => resolve({
      server,
      state: () => fetch(`http://127.0.0.1:${port}/__audit/state`).then((r) => r.json()).catch(() => null),
      arm: (after) => fetch(`http://127.0.0.1:${port}/__audit/arm?after=${encodeURIComponent(after)}`, { method: 'POST' }),
      release: () => fetch(`http://127.0.0.1:${port}/__audit/release`, { method: 'POST' }),
    }));
  });
}

function startVite(port, apiUrl) {
  const child = spawn('npm', ['run', 'dev', '--', '--host', '127.0.0.1', '--port', String(port), '--strictPort'], {
    cwd: new URL('..', import.meta.url).pathname,
    env: { ...process.env, VITE_ANALYST_BASE: '/', VITE_ANALYST_DEMO_TOKEN: 'demo:acme_labs:peter', CC_API_URL: apiUrl },
    stdio: ['ignore', 'pipe', 'pipe'],
  });
  let log = '';
  child.stdout.on('data', (d) => { log += d.toString(); });
  child.stderr.on('data', (d) => { log += d.toString(); });
  return { child, ready: (async () => {
    for (let i = 0; i < 50; i++) {
      try {
        const r = await fetch(`http://127.0.0.1:${port}/`);
        if (r.ok) return;
      } catch { /* not up */ }
      await delay(200);
    }
    throw new Error(`vite did not start\n${log}`);
  })() };
}

async function injectAxe(page) {
  // The app's CSP allows scripts only with the document nonce, so axe cannot be an inline tag.
  const nonce = await page.evaluate(() => {
    const s = document.querySelector('script[nonce], script[src]');
    return s?.nonce || s?.getAttribute('nonce') || '';
  });
  const source = fs.readFileSync(axePath, 'utf8');
  await page.evaluate(({ source, nonce }) => {
    const el = document.createElement('script');
    if (nonce) el.nonce = nonce;
    el.textContent = source;
    document.head.appendChild(el);
  }, { source, nonce });
  await page.waitForFunction(() => typeof window.axe?.run === 'function');
}

async function runAxe(page, label) {
  const result = await page.evaluate(async () => {
    const r = await window.axe.run(document, { resultTypes: ['violations', 'incomplete'] });
    const slim = (list) => list.map((v) => ({
      id: v.id,
      impact: v.impact,
      help: v.help,
      nodeCount: v.nodes.length,
      targets: v.nodes.slice(0, 6).map((n) => n.target.join(' ')),
      sample: v.nodes.slice(0, 3).map((n) => ({
        target: n.target.join(' '),
        html: (n.html || '').slice(0, 160),
        failure: (n.failureSummary || '').split('\n').slice(0, 4).join(' | '),
      })),
    }));
    return { violations: slim(r.violations), incomplete: slim(r.incomplete.filter((v) => v.id === 'color-contrast')) };
  });
  const serious = result.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  console.log(`\n== ${label}: ${result.violations.length} violations, ${serious.length} serious/critical`);
  for (const v of result.violations) {
    if (v.impact !== 'serious' && v.impact !== 'critical') console.log(`  [${v.impact}] ${v.id} x${v.nodeCount}`);
  }
  for (const v of serious) {
    console.log(`  [${v.impact}] ${v.id} x${v.nodeCount} — ${v.help}`);
    for (const s of v.sample) console.log(`     ${s.target} :: ${s.failure}`);
  }
  return { label, ...result };
}

async function motionProbe(page) {
  return page.evaluate(() => {
    const el = document.createElement('div');
    el.className = 'shimmer';
    el.textContent = 'Thinking…';
    document.body.appendChild(el);
    const cs = getComputedStyle(el);
    const caret = document.createElement('div');
    caret.className = 'streaming-caret';
    document.body.appendChild(caret);
    const before = getComputedStyle(caret, '::after');
    const out = {
      reduced: window.matchMedia('(prefers-reduced-motion: reduce)').matches,
      shimmerName: cs.animationName,
      shimmerDuration: cs.animationDuration,
      shimmerIteration: cs.animationIterationCount,
      shimmerColor: cs.color,
      caretDuration: before.animationDuration,
      caretName: before.animationName,
    };
    el.remove();
    caret.remove();
    return out;
  });
}

async function tabProbe(page, steps) {
  const out = [];
  for (let i = 0; i < steps; i++) {
    await page.keyboard.press('Tab');
    out.push(await page.evaluate(() => {
      const el = document.activeElement;
      if (!el || el === document.body) return { tag: 'BODY' };
      const s = getComputedStyle(el);
      return {
        tag: el.tagName,
        name: (el.getAttribute('aria-label') || el.textContent || '').trim().slice(0, 60),
        opacity: s.opacity,
        display: s.display,
      };
    }));
  }
  return out;
}

/** Theme and style moved into the header menu and Settings on the split shell. */
async function chooseMenu(page, item) {
  await page.getByRole('button', { name: 'More options' }).click();
  await page.getByRole('menuitem', { name: item }).click();
}

async function setMode(page, mode) {
  await chooseMenu(page, mode === 'dark' ? 'Dark mode' : 'Light mode');
}

async function setPalette(page, palette) {
  await chooseMenu(page, 'Settings');
  await page.getByRole('radio', { name: palette === 'paper' ? 'Paper' : 'Neutral' }).click();
  await page.keyboard.press('Escape');
}

async function main() {
  const proxy = await createHoldProxy(8799);
  let vite = null;
  let base = APP;
  if (!base) {
    vite = startVite(5174, 'http://127.0.0.1:8799');
    await vite.ready;
    base = 'http://127.0.0.1:5174';
  }
  console.log('app', base);

  const browser = await chromium.launch({
    executablePath: process.env.CHROME_PATH || '/usr/local/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage', '--js-flags=--expose-gc'],
  });
  const reports = [];
  const notes = {};

  const desktop = await browser.newPage({ viewport: { width: 1360, height: 900 } });
  const errors = [];
  desktop.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  desktop.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
  await desktop.goto(base);
  await desktop.getByRole('heading', { name: 'What should we look into?' }).waitFor();
  await injectAxe(desktop);

  reports.push(await runAxe(desktop, 'empty-light-sidebar'));
  notes.tabEmpty = await tabProbe(desktop, 8);

  await setMode(desktop, 'dark');
  reports.push(await runAxe(desktop, 'empty-dark'));
  await setMode(desktop, 'light');

  await chooseMenu(desktop, 'Settings');
  await desktop.getByRole('dialog', { name: 'Settings' }).waitFor();
  reports.push(await runAxe(desktop, 'settings-light'));
  await desktop.getByRole('radio', { name: 'Paper' }).click();
  await desktop.keyboard.press('Escape');
  reports.push(await runAxe(desktop, 'empty-paper-light'));
  await setMode(desktop, 'dark');
  reports.push(await runAxe(desktop, 'empty-paper-dark'));
  await setMode(desktop, 'light');
  await setPalette(desktop, 'neutral');

  await desktop.getByRole('button', { name: 'Search chats' }).click();
  await desktop.getByRole('combobox', { name: 'Search chats' }).waitFor();
  reports.push(await runAxe(desktop, 'sidebar-search'));
  await desktop.keyboard.press('Escape');

  await desktop.getByRole('button', { name: 'Close sidebar' }).click();
  reports.push(await runAxe(desktop, 'sidebar-collapsed'));
  await desktop.getByRole('button', { name: 'Open sidebar' }).click();

  await desktop.emulateMedia({ reducedMotion: 'reduce' });
  notes.reducedMotion = await motionProbe(desktop);
  reports.push(await runAxe(desktop, 'empty-reduced-motion'));
  await desktop.emulateMedia({ reducedMotion: 'no-preference' });
  notes.motionDefault = await motionProbe(desktop);

  // Streaming against the held demo SSE.
  const box = desktop.getByRole('textbox', { name: 'Message' });
  await box.fill('What is the denial rate by payer?');
  await box.press('Enter');
  await desktop.getByText('Analyzing').waitFor({ timeout: 20000 });
  reports.push(await runAxe(desktop, 'streaming-status-light'));
  await setMode(desktop, 'dark');
  reports.push(await runAxe(desktop, 'streaming-status-dark'));

  await proxy.arm('text.delta');
  await desktop.locator('.streaming-caret').waitFor({ timeout: 20000 });
  reports.push(await runAxe(desktop, 'streaming-text-dark'));
  await setMode(desktop, 'light');
  reports.push(await runAxe(desktop, 'streaming-text-light'));
  notes.streamState = await proxy.state();
  await delay(400);
  notes.liveRegion = await desktop.evaluate(() => {
    const status = document.querySelector('[role="status"]');
    const turn = document.querySelector('[data-testid="msg-assistant"]');
    return {
      status: status ? { live: status.getAttribute('aria-live'), atomic: status.getAttribute('aria-atomic'), text: (status.textContent || '').slice(0, 160) } : null,
      turnLive: turn?.getAttribute('aria-live') || null,
      turnBusy: turn?.getAttribute('aria-busy') || null,
    };
  });

  await proxy.release();
  await desktop.getByTestId('data-table').waitFor({ timeout: 20000 });
  await desktop.getByTestId('chart').waitFor({ timeout: 20000 });
  await desktop.getByTestId('msg-actions').waitFor({ timeout: 20000 });
  // Let Vega finish painting.
  await delay(600);
  reports.push(await runAxe(desktop, 'table-chart-light'));
  await setMode(desktop, 'dark');
  await delay(400);
  reports.push(await runAxe(desktop, 'table-chart-dark'));
  await setPalette(desktop, 'paper');
  await delay(400);
  reports.push(await runAxe(desktop, 'table-chart-paper-dark'));
  await setMode(desktop, 'light');
  await delay(300);
  reports.push(await runAxe(desktop, 'table-chart-paper-light'));
  await setPalette(desktop, 'neutral');

  await desktop.getByRole('button', { name: 'Bad response' }).click();
  await desktop.getByTestId('feedback-panel').waitFor();
  reports.push(await runAxe(desktop, 'feedback-panel'));
  await desktop.getByRole('button', { name: 'Close feedback' }).click();

  const expandTable = desktop.getByRole('button', { name: 'Expand table' });
  if (await expandTable.count()) {
    await expandTable.first().click({ force: true });
    await desktop.getByTestId('artifact-panel').waitFor();
    reports.push(await runAxe(desktop, 'artifact-table'));
    await desktop.getByRole('button', { name: 'Close panel' }).click();
  }
  const expandChart = desktop.getByRole('button', { name: 'Expand chart' });
  if (await expandChart.count()) {
    await expandChart.first().click({ force: true });
    await desktop.getByTestId('artifact-panel').waitFor();
    await delay(400);
    reports.push(await runAxe(desktop, 'artifact-chart'));
    await desktop.getByRole('tab', { name: 'Spec' }).click();
    reports.push(await runAxe(desktop, 'artifact-chart-spec'));
    await desktop.getByRole('button', { name: 'Full screen' }).click();
    reports.push(await runAxe(desktop, 'artifact-fullscreen'));
    await desktop.keyboard.press('Escape');
  }

  // Mobile drawer, empty and over a thread.
  const phone = await browser.newPage({ viewport: { width: 390, height: 844 }, hasTouch: true });
  await phone.goto(base);
  await phone.getByRole('heading', { name: 'What should we look into?' }).waitFor();
  await phone.getByRole('button', { name: 'Open sidebar' }).click();
  await phone.getByTestId('drawer-scrim').waitFor();
  await injectAxe(phone);
  reports.push(await runAxe(phone, 'mobile-drawer-light'));
  await phone.keyboard.press('Escape');
  if (await phone.getByTestId('sidebar').count()) await phone.getByRole('button', { name: 'Close sidebar' }).click();
  await setMode(phone, 'dark');
  await phone.getByRole('button', { name: 'Open sidebar' }).click();
  await phone.getByTestId('drawer-scrim').waitFor();
  reports.push(await runAxe(phone, 'mobile-drawer-dark'));
  const drawerFocus = await phone.evaluate(() => document.activeElement?.getAttribute('aria-label') || document.activeElement?.textContent?.trim()?.slice(0, 40) || null);
  await phone.keyboard.press('Escape');
  const afterEsc = await phone.evaluate(() => ({
    open: !!document.querySelector('[data-testid="sidebar"]'),
    focus: document.activeElement?.getAttribute('aria-label') || document.activeElement?.tagName,
  }));
  notes.mobileDrawer = { focusOnOpen: drawerFocus, afterEscape: afterEsc };
  await setMode(phone, 'light');

  // Keyboard-only send and drawer.
  notes.keyboardSend = await (async () => {
    await phone.keyboard.press('Tab');
    const steps = [];
    for (let i = 0; i < 12; i++) {
      const cur = await phone.evaluate(() => ({
        name: document.activeElement?.getAttribute('aria-label') || document.activeElement?.textContent?.trim().slice(0, 40),
        tag: document.activeElement?.tagName,
      }));
      steps.push(cur);
      if (cur.name === 'Message') break;
      await phone.keyboard.press('Tab');
    }
    return steps;
  })();

  await browser.close();
  if (vite) vite.child.kill('SIGTERM');
  proxy.server.closeAllConnections?.();
  proxy.server.close();

  const serious = [];
  for (const r of reports) {
    for (const v of r.violations) {
      if (v.impact === 'serious' || v.impact === 'critical') serious.push({ screen: r.label, ...v });
    }
  }
  const byRule = {};
  for (const v of serious) {
    byRule[v.id] ??= { impact: v.impact, screens: new Set(), nodes: 0 };
    byRule[v.id].screens.add(v.screen);
    byRule[v.id].nodes += v.nodeCount;
  }
  const summary = Object.entries(byRule).map(([id, v]) => ({ id, impact: v.impact, nodes: v.nodes, screens: [...v.screens] }));
  const payload = { base, summary, notes, consoleErrors: errors.slice(0, 20), reports };
  fs.writeFileSync(OUT, JSON.stringify(payload, null, 2));
  console.log('\nSUMMARY serious/critical');
  for (const s of summary) console.log(`  ${s.impact} ${s.id}: ${s.nodes} nodes across ${s.screens.join(', ')}`);
  console.log('console errors', errors.length);
  console.log('wrote', OUT);
  setTimeout(() => process.exit(process.exitCode ?? 0), 20);
}

main().catch((e) => { console.error(e); process.exit(1); });
