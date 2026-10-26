// Partial-table streaming e2e: records the visible row count of the answer table over time.
// Usage: node scripts/e2e_answer_table_reveal.mjs [baseUrl] [outDir] [question]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer', q = 'Top denial reason codes last quarter'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const pg = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const errors = [];
pg.on('pageerror', (e) => errors.push(String(e)));
await pg.goto(base, { timeout: 90000 });
const box = pg.getByRole('textbox', { name: 'Message' });
await box.waitFor({ timeout: 90000 });
await pg.evaluate(() => {
  window.__rows = [];
  const t0 = performance.now();
  new MutationObserver(() => {
    const t = [...document.querySelectorAll('[data-testid="msg-assistant"] table tbody')].at(-1);
    if (!t) return;
    const n = t.querySelectorAll('tr:not([aria-hidden])').length;
    const last = window.__rows.at(-1);
    if (!last || last[1] !== n) window.__rows.push([Math.round(performance.now() - t0), n]);
  }).observe(document.body, { subtree: true, childList: true });
});
await box.fill(q);
await pg.keyboard.press('Enter');
let shot = false;
for (let i = 0; i < 400 && !shot; i++) {
  const rows = await pg.evaluate(() => window.__rows);
  if (rows.length >= 2) { await pg.screenshot({ path: `${out}/stream_07_table_reveal_mid.png` }); shot = true; }
  await pg.waitForTimeout(10);
}
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
await pg.waitForTimeout(500);
await pg.screenshot({ path: `${out}/stream_08_table_reveal_done.png` });
const rows = await pg.evaluate(() => window.__rows);
console.log(JSON.stringify({ rowTimeline: rows, spanMs: rows.length ? rows.at(-1)[0] - rows[0][0] : null, final: rows.at(-1)?.[1], footer: await pg.getByText(/Showing \d+ of/).last().textContent().catch(() => null), pageErrors: errors }, null, 0));
await browser.close();
