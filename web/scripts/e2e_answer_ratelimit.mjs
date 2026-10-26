// 429 auto-retry e2e: vite preview (built with VITE_DEMO_FAULTS=1) + demo API. `?fault=429` makes the
// first try of each answer return 429 + Retry-After: 3 from the demo API; the client counts down and retries.
// Usage: node scripts/e2e_answer_ratelimit.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const pg = await (await browser.newContext({ viewport: { width: 1280, height: 900 } })).newPage();
const errors = [];
pg.on('pageerror', (e) => errors.push(String(e)));
const chats = [];
pg.on('request', (r) => { if (r.url().endsWith('/api/chat') && r.method() === 'POST') chats.push({ t: Date.now(), fault: r.headers()['x-demo-fault'] ?? null }); });
const box = () => pg.getByRole('textbox', { name: 'Message' });
await pg.goto(`${base}?fault=429`, { timeout: 90000 });
await box().waitFor({ timeout: 90000 });
await box().fill('Denial rate by payer');
await pg.keyboard.press('Enter');
const seen = new Set();
const t0 = Date.now();
let shot = false;
while (Date.now() - t0 < 8000) {
  const s = (await pg.getByTestId('status').last().textContent({ timeout: 200 }).catch(() => null))?.trim();
  if (s) seen.add(s);
  if (s?.startsWith('Busy right now') && !shot) { await pg.screenshot({ path: `${out}/plumb_05_rate_limit_countdown.png` }); shot = true; }
  if (chats.length >= 2) break;
  await pg.waitForTimeout(150);
}
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
await pg.waitForTimeout(600);
await pg.screenshot({ path: `${out}/plumb_06_rate_limit_recovered.png` });
const result = {
  statuses: [...seen],
  requests: chats.map((c) => ({ fault: c.fault, atMs: c.t - chats[0].t })),
  waitedMs: chats.length > 1 ? chats[1].t - chats[0].t : null,
  answerMeta: (await pg.getByTestId('answer-meta').last().textContent().catch(() => null)),
  errorCards: await pg.getByText('Too many requests').count(),
  pageErrors: errors,
};
console.log(JSON.stringify(result, null, 1));
await browser.close();
