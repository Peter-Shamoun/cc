// Mock-mode gallery of answer states for side-by-side review (no API). Usage:
// node scripts/e2e_answer_gallery.mjs [baseUrl] [outDir] [prefix]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer', prefix = 'g'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const errors = [];
const prompts = [
  ['dup', 'Which providers have the highest duplicate-claim denial rate?'],
  ['abstain', 'What is our patient satisfaction score by clinic?'],
  ['clarify', 'Who are our top providers?'],
  ['error', 'Run the query against the raw 837 tables'],
  ['trend', 'Plot the monthly denial rate trend for our top 3 payers'],
  ['payer', 'What was our denial rate by payer last quarter?'],
  ['repair', 'Which billing providers have the highest denial rate this year?'],
];
for (const [name, q] of prompts) {
  const pg = await (await browser.newContext({ viewport: { width: 1024, height: 900 } })).newPage();
  pg.on('pageerror', (e) => errors.push(`${name}: ${e}`));
  await pg.goto(base, { timeout: 90000 });
  const box = pg.getByRole('textbox', { name: 'Message' });
  await box.waitFor({ timeout: 90000 });
  await box.fill(q);
  await pg.keyboard.press('Enter');
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
  await pg.waitForTimeout(700);
  const a = pg.locator('[data-testid="msg-assistant"]').last();
  await a.screenshot({ path: `${out}/${prefix}_${name}.png` });
  await pg.close();
}
console.log(JSON.stringify({ errors }));
await browser.close();
