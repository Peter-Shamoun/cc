// Screenshots of the running dev server (npm run dev) with Playwright.
// Usage: node scripts/screenshots.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';

const base = process.argv[2] ?? 'http://127.0.0.1:5173/';
const out = process.argv[3] ?? '../../shots';
fs.mkdirSync(out, { recursive: true });
const exe = process.env.CHROME_PATH ?? (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined);
const browser = await chromium.launch({ executablePath: exe, headless: true });
const consoleLines = [];

async function page({ palette = 'neutral', theme = 'light', w = 1440, h = 900 } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  await ctx.addInitScript(([p, t]) => { localStorage.setItem('cc.pref.palette', p); localStorage.setItem('cc.pref.theme', t); }, [palette, theme]);
  const pg = await ctx.newPage();
  pg.on('console', (m) => consoleLines.push(m.text()));
  await pg.goto(base);
  return pg;
}
async function ask(pg, prompt) {
  await pg.getByRole('textbox', { name: 'Message' }).fill(prompt);
  await pg.keyboard.press('Enter');
  await pg.waitForTimeout(300);
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 30000 });
  await pg.waitForTimeout(1200);
}
const shot = (pg, name, full = false) => pg.screenshot({ path: `${out}/${name}.png`, fullPage: full });

let pg = await page();
await shot(pg, '01_empty_neutral_light');
// mid-stream
await pg.getByRole('textbox', { name: 'Message' }).fill('What was our denial rate by payer last quarter?');
await pg.keyboard.press('Enter');
await pg.waitForTimeout(1500);
await shot(pg, '02_streaming_neutral_light');
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 30000 });
await pg.waitForTimeout(1200);
await pg.locator('[data-testid="reasoning"] button').first().click();
await pg.locator('[data-testid="sql-disclosure"] button').first().click();
await shot(pg, '03_denial_by_payer_neutral_light');
await pg.locator('[data-testid="data-table"]').first().hover();
await pg.getByLabel('Expand table').first().click();
await pg.waitForTimeout(500);
await shot(pg, '04_artifact_panel_table');
await pg.getByRole('tab', { name: 'SQL' }).click();
await pg.waitForTimeout(500);
await shot(pg, '05_artifact_panel_sql');
await pg.getByLabel('Close panel').click();

await ask(pg, 'Plot the monthly denial rate trend for our top 3 payers');
await pg.waitForTimeout(800);
await shot(pg, '06_chart_and_image_neutral_light');
await pg.locator('[data-testid="chart"]').last().hover();
await pg.getByLabel('Expand chart').last().click();
await pg.waitForTimeout(1200);
await shot(pg, '07_artifact_panel_chart');
await pg.getByLabel('Close panel').click();

await ask(pg, 'Which CARC codes drive Cascade Medicaid denials?');
await shot(pg, '08_carc_masked_table');
await ask(pg, 'Run the query against the raw 837 tables');
await shot(pg, '09_error_part');
await pg.close();

for (const [palette, theme, name] of [['neutral', 'dark', '10_neutral_dark'], ['paper', 'light', '11_paper_light'], ['paper', 'dark', '12_paper_dark']]) {
  pg = await page({ palette, theme });
  await shot(pg, `${name}_empty`);
  await ask(pg, 'What was our denial rate by payer last quarter?');
  await shot(pg, `${name}_answer`);
  await ask(pg, 'Plot the monthly denial rate trend for our top 3 payers');
  await pg.waitForTimeout(800);
  await shot(pg, `${name}_chart`);
  await pg.close();
}

pg = await page({ w: 390, h: 844 });
await ask(pg, 'What was our denial rate by payer last quarter?');
await shot(pg, '13_mobile_390');
await pg.close();

// PHI check: nothing but allowlisted prefs in storage, no row data in console.
pg = await page();
await ask(pg, 'Which CARC codes drive Cascade Medicaid denials?');
const keys = await pg.evaluate(() => Object.keys(localStorage));
const url = pg.url();
console.log('localStorage keys:', keys, 'url:', url);
const leaked = consoleLines.filter((l) => /Cascade|SYN-|ZZ\d/.test(l));
console.log('console lines:', consoleLines.length, 'with row data:', leaked.length);
await browser.close();
