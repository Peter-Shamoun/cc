// QA-A7/A8/A10/A11/A14 e2e: one live "Thinking…" row (no header copy of the running step, no
// duplicate status line), one "Thought for Ns" row when done that expands into counts + reasoning
// + steps, unique group names, compact durations, clarify question rendered as markdown.
// Usage: node scripts/e2e_answer_timeline.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const pg = await (await browser.newContext({ viewport: { width: 1024, height: 640 } })).newPage();
const errors = [];
pg.on('pageerror', (e) => errors.push(String(e)));
const box = () => pg.getByRole('textbox', { name: 'Message' });
const idle = () => pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
await pg.goto(base, { timeout: 90000 });
await box().waitFor({ timeout: 90000 });
await box().fill('Which CARC codes drive denials?');
await pg.keyboard.press('Enter');
const live = { maxDupes: 0, headers: new Set(), statusWhileLive: 0 };
const t0 = Date.now();
let shot = false;
while (Date.now() - t0 < 15000) {
  const snap = await pg.evaluate(() => {
    const tl = [...document.querySelectorAll('[data-testid="step-timeline"]')].pop();
    if (!tl) return null;
    const rows = [...tl.querySelectorAll('[data-testid="step"]')].map((r) => r.textContent.replace(/[✓\d.]+m?s.*$/, '').trim());
    return { live: tl.dataset.live === 'true', header: tl.querySelector('[data-testid="step-summary"]')?.textContent, rows, status: !!document.querySelector('[data-testid="status"]') };
  });
  if (snap?.live) {
    live.headers.add(snap.header.replace(/\d+(\.\d)?m?s$/, ''));
    const counts = new Map();
    snap.rows.forEach((r) => counts.set(r, (counts.get(r) ?? 0) + 1));
    live.maxDupes = Math.max(live.maxDupes, ...[...counts.values()].map((n) => n - 1), 0);
    if (snap.status) live.statusWhileLive++;
    if (!shot && snap.rows.length >= 2) { await pg.screenshot({ path: `${out}/c3_04_live_thinking_row.png` }); shot = true; }
  }
  if (await pg.getByRole('button', { name: 'Send message' }).isVisible().catch(() => false)) break;
  await pg.waitForTimeout(120);
}
await idle();
await pg.waitForTimeout(500);
const head = pg.getByTestId('step-summary').last();
const collapsed = await head.textContent();
const reasoningRows = await pg.getByTestId('reasoning').count();
await pg.screenshot({ path: `${out}/c3_05_done_one_row.png` });
await head.click();
await pg.waitForTimeout(300);
const expanded = await head.textContent();
await pg.screenshot({ path: `${out}/c3_06_expanded_counts_reasoning.png` });
const viewSql = await pg.getByTestId('table-sql').first().textContent().catch(() => null);
await pg.getByRole('button', { name: 'New chat' }).first().click().catch(() => {});
await box().fill('How bad are our denials?');
await pg.keyboard.press('Enter');
await idle();
await pg.waitForTimeout(500);
const q = await pg.getByTestId('clarify-question').last().evaluate((el) => ({ text: el.textContent, strong: el.querySelectorAll('strong').length })).catch(() => null);
await pg.getByTestId('clarify-question').last().scrollIntoViewIfNeeded().catch(() => {});
await pg.screenshot({ path: `${out}/c3_07_clarify_markdown.png` });
const groups = await pg.evaluate(() => [...document.querySelectorAll('[data-testid="step-timeline"]')].map((g) => ({ role: g.getAttribute('role'), by: g.getAttribute('aria-labelledby') })));
console.log(JSON.stringify({ live: { ...live, headers: [...live.headers] }, collapsed, expanded, reasoningRows, viewSql, clarify: q, groups, pageErrors: errors }, null, 1));
await browser.close();
