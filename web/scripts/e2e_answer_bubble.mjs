// Gap between the question bubble and the answer's first row, and long-question collapse.
// Usage: node scripts/e2e_answer_bubble.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const pg = await (await browser.newContext({ viewport: { width: 1024, height: 700 } })).newPage();
const errors = [];
pg.on('pageerror', (e) => errors.push(String(e)));
const box = () => pg.getByRole('textbox', { name: 'Message' });
const idle = () => pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
await pg.goto(base, { timeout: 90000 });
await box().waitFor({ timeout: 90000 });
await box().fill('Which CARC codes drive denials?');
await pg.keyboard.press('Enter');
await idle();
await pg.waitForTimeout(400);
const gap = await pg.evaluate(() => {
  const u = document.querySelector('[data-testid="msg-user"] [data-testid="user-text"]').parentElement.getBoundingClientRect();
  const a = document.querySelector('[data-testid="msg-assistant"]').firstElementChild.getBoundingClientRect();
  return Math.round(a.top - u.bottom);
});
await pg.screenshot({ path: `${out}/d1_01_question_gap.png` });
await pg.hover('[data-testid="msg-user"] [data-testid="user-text"]');
await pg.waitForTimeout(250);
await pg.screenshot({ path: `${out}/d1_02_question_hover_actions.png` });
// Synthetic paste: 40 fake claim lines.
const paste = 'Summarize these denials:\n' + Array.from({ length: 40 }, (_, i) => `CLM-SYN-${1000 + i}, PAYER-${'ABC'[i % 3]}, CARC CO-${[45, 97, 16][i % 3]}, $${(120 + i * 7).toFixed(2)}`).join('\n');
await box().fill(paste);
await pg.keyboard.press('Enter');
await idle();
const last = pg.locator('[data-testid="msg-user"]').last();
await last.scrollIntoViewIfNeeded();
const collapsed = await last.locator('[data-testid="user-text"]').evaluate((e) => Math.round(e.getBoundingClientRect().height));
await last.locator('[data-testid="user-text"]').scrollIntoViewIfNeeded();
await pg.screenshot({ path: `${out}/d1_03_long_question_collapsed.png` });
await last.getByTestId('user-show-more').click();
const expanded = await last.locator('[data-testid="user-text"]').evaluate((e) => Math.round(e.getBoundingClientRect().height));
await last.getByTestId('user-show-more').scrollIntoViewIfNeeded();
await pg.screenshot({ path: `${out}/d1_04_long_question_expanded.png` });
const res = { gapPx: gap, collapsedPx: collapsed, expandedPx: expanded, errors };
console.log(JSON.stringify(res));
await browser.close();
const ok = gap <= 32 && collapsed < 260 && expanded > collapsed * 2 && !errors.length;
process.exit(ok ? 0 : 1);
