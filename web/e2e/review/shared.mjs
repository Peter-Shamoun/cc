// QA review of the read-only /shared/:id page (#229): data off/on, light/dark, 1440/390, unavailable; axe + checks.
// Usage (demo API on 8140): node e2e/review/shared.mjs <outDir>
import { chromium } from 'playwright';
import fs from 'node:fs';
import { createRequire } from 'node:module';
import { distRoutes, LIVE } from '../static.mjs';
const require = createRequire(import.meta.url);
const axeSrc = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
const out = process.argv[2]; fs.mkdirSync(out, { recursive: true });
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome' });
const res = {};
const mk = async (vp, theme) => { const ctx = await b.newContext({ viewport: vp, colorScheme: theme, isMobile: vp.width < 500, hasTouch: vp.width < 500, deviceScaleFactor: vp.width < 500 ? 2 : 1 }); await distRoutes(ctx); await ctx.grantPermissions(['clipboard-read', 'clipboard-write']); await ctx.addInitScript((t) => { try { localStorage.setItem('cc.theme', t); } catch {} }, theme); return ctx; };
const owner = await mk({ width: 1440, height: 900 }, 'light'); const p = await owner.newPage(); await p.goto(LIVE);
const ask = async (q) => { const box = p.getByRole('textbox', { name: 'Message' }); await box.fill(q); await box.press('Enter'); await p.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 }); };
await ask('Total paid by payer'); await ask('Plot the monthly denial rate trend for our top 3 payers');
const link = async (data) => { await p.getByTestId('share-button').click(); const dlg = p.getByRole('dialog', { name: 'Share chat' });
  await dlg.waitFor(); await p.waitForTimeout(1000);
  if (!(await dlg.getByTestId('share-url').count())) await dlg.getByTestId('create-link').click();
  const sw = dlg.getByRole('switch', { name: 'Include result data' }); await sw.waitFor();
  if ((await sw.isChecked()) !== data) { await sw.click(); await p.waitForTimeout(600); }
  const url = await dlg.getByTestId('share-url').inputValue();
  await p.screenshot({ path: `${out}/share-dialog-${data ? 'data' : 'nodata'}.png` }); await p.keyboard.press('Escape'); return url; };
for (const data of [false, true]) {
  const url = await link(data);
  for (const [vp, tag] of [[{ width: 1440, height: 900 }, 'desktop'], [{ width: 390, height: 844 }, 'mobile']]) for (const theme of ['light', 'dark']) {
    const ctx = await mk(vp, theme); const v = await ctx.newPage(); const errs = []; v.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 120)); });
    await v.goto(url); await v.getByTestId('shared-view').waitFor({ timeout: 20000 }); await v.waitForTimeout(800);
    const name = `shared-${data ? 'data' : 'nodata'}-${tag}-${theme}`;
    await v.screenshot({ path: `${out}/${name}.png`, fullPage: true });
    await v.addScriptTag({ content: axeSrc }); const ax = await v.evaluate(async () => (await window.axe.run(document, { runOnly: ['wcag2a', 'wcag2aa'] })).violations.map((x) => `${x.id}(${x.nodes.length})`));
    const info = await v.evaluate(() => ({ title: document.title, h1: document.querySelector('h1')?.textContent, tables: document.querySelectorAll('table').length, charts: document.querySelectorAll('svg[role=img],[data-testid=chart] svg').length, composer: !!document.querySelector('textarea'), scrollW: document.documentElement.scrollWidth, landmarks: [...document.querySelectorAll('main,header,nav,aside,footer')].map((e) => e.tagName.toLowerCase()), url: location.href.replace(/\/shared\/[^/?#]+/, '/shared/:id') }));
    res[name] = { axe: ax, errs, ...info };
    await ctx.close();
  }
}
const ctx = await mk({ width: 1440, height: 900 }, 'light'); const v = await ctx.newPage(); await v.goto(new URL('/shared/sh_doesnotexist', LIVE).href); await v.getByTestId('shared-unavailable').waitFor(); await v.screenshot({ path: `${out}/shared-unavailable.png` }); await ctx.close();
fs.writeFileSync(`${out}/shared.json`, JSON.stringify(res, null, 1)); console.log(JSON.stringify(res, null, 1));
await b.close();
