// Real server 429 (platform #160): with CC_API_MAX_CONCURRENT_RUNS=1, a second tab's question gets 429 +
// Retry-After while the first answer runs; the client counts down (visible + polite live region), retries on its
// own and answers. Usage: node scripts/e2e_answer_429.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const ctx = await browser.newContext({ viewport: { width: 1024, height: 640 } });
const errors = [];
const open = async () => { const p = await ctx.newPage(); p.on('pageerror', (e) => errors.push(String(e))); await p.goto(base, { timeout: 90000 }); await p.getByRole('textbox', { name: 'Message' }).waitFor({ timeout: 90000 }); return p; };
const a = await open();
const b = await open();
const statuses = [];
b.on('response', (r) => { if (r.url().endsWith('/api/chat')) statuses.push(r.status()); });
await a.getByRole('textbox', { name: 'Message' }).fill('Which CARC codes drive denials?');
await a.keyboard.press('Enter');
await a.locator('[data-testid="msg-assistant"]').waitFor();
await a.waitForTimeout(300);
await b.getByRole('textbox', { name: 'Message' }).fill('Denial rate by payer');
await b.evaluate(() => { window.__said = []; new MutationObserver(() => { const t = document.querySelector('[data-testid="transport-announcer"]')?.textContent; if (t && window.__said.at(-1) !== t) window.__said.push(t); }).observe(document.body, { subtree: true, childList: true, characterData: true }); });
await b.keyboard.press('Enter');
const said = new Set();
let shot = false;
const t0 = Date.now();
while (Date.now() - t0 < 60000) {
  const s = await b.evaluate(() => ({ live: document.querySelector('[data-testid="transport-announcer"]')?.textContent ?? '', status: document.querySelector('[data-testid="status"]')?.textContent ?? '' }));
  if (s.live) said.add(s.live);
  if (!shot && /retrying in \d+s/.test(s.status)) { await b.screenshot({ path: `${out}/d2_01_429_countdown.png` }); shot = true; }
  if (await b.getByRole('button', { name: 'Send message' }).isVisible().catch(() => false)) break;
  await b.waitForTimeout(150);
}
await b.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
await b.waitForTimeout(300);
await b.screenshot({ path: `${out}/d2_02_429_answered.png` });
const answered = await b.locator('[data-testid="msg-assistant"] table').count();
const res = { statuses, said: await b.evaluate(() => window.__said), countdownShot: shot, answered, errors };
console.log(JSON.stringify(res));
await browser.close();
process.exit(statuses.includes(429) && statuses.includes(200) && said.size > 0 && answered > 0 && !errors.length ? 0 : 1);
