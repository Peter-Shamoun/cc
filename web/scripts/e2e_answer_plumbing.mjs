// Answer-plumbing e2e against vite preview + demo API (ScriptedLLM, synthetic data only).
// Usage: node scripts/e2e_answer_plumbing.mjs [baseUrl] [outDir]
// Checks: Stop keeps usage figures; a replied clarification shows as answered after reload;
// Regenerate does not duplicate the question after reload.
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1280, height: 1000 } });
const pg = await ctx.newPage();
const errors = [];
pg.on('pageerror', (e) => errors.push(String(e)));
const results = {};
const shot = (n) => pg.screenshot({ path: `${out}/plumb_${n}.png` });
const box = () => pg.getByRole('textbox', { name: 'Message' });
const idle = () => pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
async function ask(text) { await box().fill(text); await pg.keyboard.press('Enter'); }
async function newChat() { await pg.goto(base, { timeout: 90000 }); await box().waitFor({ timeout: 90000 }); }
async function reopenFirstThread() {
  await pg.reload({ timeout: 90000 });
  await pg.getByTestId('thread-item').first().click();
  await pg.waitForTimeout(1500);
}

// 1) Stop keeps usage
await newChat();
await ask('Top denial reason codes last quarter');
await pg.getByRole('button', { name: 'Stop generating' }).waitFor({ timeout: 30000 });
await pg.waitForTimeout(2500);
await pg.getByRole('button', { name: 'Stop generating' }).click();
await idle();
await pg.waitForTimeout(500);
results.stopMeta = (await pg.getByTestId('answer-meta').last().textContent().catch(() => null)) ?? '(none)';
await shot('01_stopped_usage');

// 2) Clarification answered after reload
await newChat();
await ask('How bad are denials?');
await idle();
await pg.getByTestId('clarify-chip').first().waitFor({ timeout: 30000 });
await pg.getByTestId('clarify-chip').first().click();
await idle();
await pg.waitForTimeout(800);
await shot('02a_clarify_replied');
await reopenFirstThread();
results.clarifyAnsweredAfterReload = await pg.getByTestId('clarify-answered').count();
results.clarifyChipsEnabledAfterReload = await pg.locator('[data-testid="clarify-chip"]:not([disabled])').count();
await shot('02b_clarify_after_reload');

// 3) Regenerate, reload: question once, newest answer shown
await newChat();
await ask('Claims by month');
await idle();
await pg.getByRole('button', { name: 'Regenerate' }).last().click();
await idle();
await pg.waitForTimeout(800);
await reopenFirstThread();
results.userTurnsAfterRegenerateReload = await pg.getByText('Claims by month', { exact: true }).count();
await shot('03_regenerate_after_reload');

results.pageErrors = errors;
console.log(JSON.stringify(results, null, 2));
await browser.close();
