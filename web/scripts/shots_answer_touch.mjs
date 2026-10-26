// Answer team: touch (hover:none) shows message/chart/code actions. node scripts/shots_answer_touch.mjs [base] [out] [tag]
import { chromium } from 'playwright';
import fs from 'node:fs';
const base = process.argv[2] ?? 'http://127.0.0.1:5212/';
const out = process.argv[3] ?? '/workspace/cc_fe/shots/answer';
const tag = process.argv[4] ?? 'qa3';
const browser = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined, headless: true });
const ctx = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
const pg = await ctx.newPage();
await pg.goto(base, { timeout: 120000 });
console.log('hover:none =', await pg.evaluate(() => matchMedia('(hover: none)').matches));
for (const q of ['Plot the monthly denial rate trend for our top 3 payers', 'What was our denial rate by payer last quarter?']) {
  await pg.getByRole('textbox', { name: 'Message' }).fill(q);
  await pg.keyboard.press('Enter');
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 90000 });
  await pg.waitForTimeout(1200);
}
const first = pg.getByTestId('msg-actions').first();
await first.scrollIntoViewIfNeeded();
await pg.waitForTimeout(400);
await pg.screenshot({ path: `${out}/${tag}_01_mobile_earlier_actions_visible.png` });
const chart = pg.getByTestId('chart').first();
await chart.scrollIntoViewIfNeeded();
await chart.screenshot({ path: `${out}/${tag}_02_mobile_chart_actions_visible.png` });
await browser.close();
