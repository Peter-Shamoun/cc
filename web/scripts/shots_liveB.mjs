// Team B live screenshots against the demo API (ScriptedLLM, synthetic data). Usage: node scripts/shots_liveB.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5202/', out = '/workspace/cc_web/shots'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const errors = [];
async function page(w = 1280, h = 1000) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h } });
  const pg = await ctx.newPage();
  pg.on('pageerror', (e) => errors.push(String(e)));
  pg.on('console', (m) => m.type() === 'error' && errors.push(m.text()));
  await pg.goto(base, { timeout: 90000 });
  return pg;
}
async function ask(pg, prompt) {
  await pg.getByRole('textbox', { name: 'Message' }).fill(prompt);
  await pg.keyboard.press('Enter');
  await pg.waitForTimeout(400);
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 90000 });
  await pg.waitForTimeout(900);
}
const shot = (pg, n) => pg.screenshot({ path: `${out}/sota_B_live_${n}.png` });
const cases = [
  ...(process.env.ALL ? [['repair', 'Top denial reason codes last quarter']] : []),
  ['escalation', 'What is our net collection rate by payer?'],
  ['candidates', 'Average paid amount by payer, double-check it'],
  ['abstain', 'Forecast denials next quarter'],
  ['error', 'Simulate an error please'],
  ['chart', 'Claims by month'],
];
for (const [name, q] of cases) {
  const pg = await page();
  await ask(pg, q);
  const tl = pg.locator('[data-testid="step-summary"]').first();
  if (await tl.count()) await tl.click().catch(() => {});
  await pg.waitForTimeout(300);
  await shot(pg, name);
  console.log(name, (await pg.locator('[data-testid="step"]').allInnerTexts()).map((t) => t.replace(/\s+/g, ' ').slice(0, 70)));
  await pg.context().close();
}
// clarify → resume
{
  const pg = await page();
  await ask(pg, 'How bad are our denials?');
  await shot(pg, 'clarify');
  const chip = pg.locator('[data-testid="clarify-chip"]').first();
  if (await chip.count()) { await chip.click(); const s = pg.locator('[data-testid="clarify-submit"]'); if (await s.count() && await s.isEnabled()) await s.click(); }
  await pg.waitForTimeout(500);
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 90000 });
  await pg.waitForTimeout(900);
  await shot(pg, 'clarify_resumed');
}
console.log('errors', errors);
await browser.close();
