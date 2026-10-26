import { chromium } from 'playwright';
import fs from 'node:fs';
const base = process.argv[2] ?? 'http://127.0.0.1:5212/';
const out = process.argv[3] ?? '/workspace/cc_fe/shots/answer';
const tag = process.argv[4] ?? 'qa';
fs.mkdirSync(out, { recursive: true });
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
async function page(w, h, theme = 'light') {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: w < 500 ? 2 : 1, hasTouch: w < 500, isMobile: w < 500 });
  await ctx.addInitScript((t) => { localStorage.setItem('cc.pref.theme', t); }, theme);
  const pg = await ctx.newPage();
  await pg.goto(base, { timeout: 120000 });
  await pg.getByRole('textbox', { name: 'Message' }).waitFor({ timeout: 60000 });
  return pg;
}
async function ask(pg, q, early) {
  await pg.getByRole('textbox', { name: 'Message' }).fill(q);
  await pg.keyboard.press('Enter');
  if (early) await early(pg);
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 90000 });
  await pg.waitForTimeout(1500);
}
const shot = (pg, n, el) => (el ? el.screenshot({ path: `${out}/${tag}_${n}.png` }) : pg.screenshot({ path: `${out}/${tag}_${n}.png` }));

// desktop: chart skeleton early + final
let pg = await page(1280, 860);
await ask(pg, 'Plot the monthly denial rate trend for our top 3 payers', async (p) => {
  try { await p.getByTestId('chart-skeleton').first().waitFor({ timeout: 20000 }); await shot(p, '01_chart_skeleton'); } catch { console.log('no skeleton caught'); }
});
await pg.getByTestId('chart').last().scrollIntoViewIfNeeded();
await shot(pg, '02_chart_final');
// feedback panel
await pg.getByRole('button', { name: 'Bad response' }).last().click();
await pg.waitForTimeout(700);
await shot(pg, '03_feedback_panel_in_view');
await pg.close();

// mobile 390
pg = await page(390, 844);
await ask(pg, 'Plot the monthly denial rate trend for our top 3 payers');
await pg.getByTestId('chart').last().scrollIntoViewIfNeeded();
await pg.waitForTimeout(500);
await shot(pg, '04_mobile_chart', pg.getByTestId('chart').last());
await ask(pg, 'What was our denial rate by payer last quarter?');
const t = pg.getByTestId('data-table').last();
await t.scrollIntoViewIfNeeded();
await shot(pg, '05_mobile_table_fade_right', t);
await t.locator('div.overflow-auto').first().evaluate((el) => { el.scrollLeft = el.scrollWidth; el.dispatchEvent(new Event('scroll')); });
await pg.waitForTimeout(300);
await shot(pg, '06_mobile_table_scrolled_fade_left', t);
await shot(pg, '07_mobile_full');
await browser.close();
