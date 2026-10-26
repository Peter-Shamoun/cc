// QA review of artifacts 1.9 (report panel, versions, autoOpen) and source chips (#232). Chromium, 1440 light/dark + 390.
// Usage (demo API on 8140): node e2e/review/artifacts.mjs <outDir>
import { chromium } from 'playwright';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { distRoutes, LIVE } from '../static.mjs';
const require = createRequire(import.meta.url);
const axeSrc = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const out = process.argv[2]; fs.mkdirSync(out, { recursive: true });
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome' });
const res = {};
for (const [vp, tag] of [[{ width: 1440, height: 900 }, 'desktop'], [{ width: 390, height: 844 }, 'mobile']]) for (const theme of ['light', 'dark']) {
  const m = vp.width < 500;
  const ctx = await b.newContext({ viewport: vp, colorScheme: theme, isMobile: m, hasTouch: m, deviceScaleFactor: m ? 2 : 1 });
  await distRoutes(ctx); await ctx.addInitScript((t) => { try { localStorage.setItem('cc.theme', t); } catch {} }, theme);
  const p = await ctx.newPage(); const errs = []; p.on('console', (x) => { if (x.type() === 'error') errs.push(x.text().slice(0, 140)); }); p.on('pageerror', (e) => errs.push('PAGEERROR ' + e.message.slice(0, 140)));
  await p.goto(LIVE);
  const box = p.getByRole('textbox', { name: 'Message' });
  const name = `${tag}-${theme}`; const r = { errs };
  await box.fill('Build a report on our denial drivers'); await box.press('Enter');
  const t0 = Date.now();
  await p.getByTestId('artifact-panel').waitFor({ timeout: 60000 }).then(() => { r.panelOpenedMs = Date.now() - t0; }).catch(() => { r.panelOpenedMs = null; });
  await p.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 }); await p.waitForTimeout(800);
  await p.screenshot({ path: `${out}/report-${name}.png` });
  r.panel = await p.evaluate(() => { const el = document.querySelector('[data-testid=artifact-panel]'); if (!el) return null; const bb = el.getBoundingClientRect();
    return { w: Math.round(bb.width), h: Math.round(bb.height), role: el.getAttribute('role'), label: el.getAttribute('aria-label'), headings: [...el.querySelectorAll('h1,h2,h3')].map((h) => h.textContent.trim().slice(0, 40)).slice(0, 8), versions: el.querySelector('[data-testid=artifact-versions]')?.textContent?.trim().slice(0, 40) ?? null, scrollW: document.documentElement.scrollWidth };
  });
  await p.addScriptTag({ content: axeSrc }).catch(() => {});
  r.axe = await p.evaluate(async () => window.axe ? (await window.axe.run(document, { runOnly: ['wcag2a', 'wcag2aa'] })).violations.map((x) => `${x.id}(${x.nodes.length})`) : 'n/a (CSP)').catch((e) => 'axe err ' + e.message.slice(0, 60));
  // Esc closes the panel and focus goes somewhere sensible.
  await p.keyboard.press('Escape'); await p.waitForTimeout(400);
  r.afterEsc = await p.evaluate(() => ({ panel: !!document.querySelector('[data-testid=artifact-panel]'), focus: document.activeElement?.getAttribute('aria-label') || document.activeElement?.tagName }));
  // Sources chip
  const chip = p.getByTestId('sources-chip').last();
  r.sourcesChip = await chip.count();
  if (r.sourcesChip) { await chip.click(); await p.waitForTimeout(500); r.sourceItems = await p.getByTestId('source-item').count(); await p.screenshot({ path: `${out}/sources-${name}.png` }); await p.keyboard.press('Escape'); await p.waitForTimeout(300); r.sourcesEscFocus = await p.evaluate(() => document.activeElement?.getAttribute('data-testid') || document.activeElement?.tagName); }
  // Version 2: ask to revise the report.
  await box.fill('Update the report to focus on CO-45'); await box.press('Enter'); await p.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 }); await p.waitForTimeout(800);
  r.panelV2 = await p.evaluate(() => document.querySelector('[data-testid=artifact-versions]')?.textContent?.trim().slice(0, 40) ?? null);
  await p.screenshot({ path: `${out}/report-v2-${name}.png` });
  res[name] = r; await ctx.close();
}
fs.writeFileSync(`${out}/artifacts.json`, JSON.stringify(res, null, 1)); console.log(JSON.stringify(res, null, 1));
await b.close();
