// Answer team: streaming word fade + message-action tooltip shots. node scripts/shots_answer_stream.mjs [base] [outDir] [tag]
import { chromium } from 'playwright';
import fs from 'node:fs';
const base = process.argv[2] ?? 'http://127.0.0.1:5212/';
const out = process.argv[3] ?? '/workspace/cc_fe/shots/answer';
const tag = process.argv[4] ?? 'qa2';
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
for (const theme of ['light', 'dark']) {
  const ctx = await browser.newContext({ viewport: { width: 1180, height: 820 } });
  await ctx.addInitScript((t) => localStorage.setItem('cc.pref.theme', t), theme);
  const pg = await ctx.newPage();
  await pg.goto(base, { timeout: 120000 });
  await pg.getByRole('textbox', { name: 'Message' }).fill('What was our denial rate by payer last quarter?');
  await pg.keyboard.press('Enter');
  await pg.locator('[data-sd-animate]').first().waitFor({ timeout: 60000 }).catch(() => console.log('no animated words seen'));
  await pg.waitForTimeout(120);
  await pg.screenshot({ path: `${out}/${tag}_01_streaming_fade_${theme}.png` });
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 90000 });
  await pg.waitForTimeout(800);
  const good = pg.getByRole('button', { name: 'Good response' }).last();
  await good.scrollIntoViewIfNeeded();
  await good.hover();
  await pg.waitForTimeout(500);
  const row = pg.getByTestId('msg-actions').last();
  const box = await row.boundingBox();
  await pg.screenshot({ path: `${out}/${tag}_02_action_tooltip_${theme}.png`, clip: { x: Math.max(0, box.x - 20), y: Math.max(0, box.y - 60), width: 520, height: 130 } });
  await ctx.close();
}
await browser.close();
