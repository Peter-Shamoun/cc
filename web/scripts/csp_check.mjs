// CSP + PHI smoke check against a running build (vite preview + api in demo mode).
// Usage: node scripts/csp_check.mjs [url] [shotDir]
// Fails (exit 1) on any CSP violation, any console error, a missing security header,
// or demo row data showing up in localStorage, sessionStorage, the URL or the console.
import { chromium } from 'playwright';
import fs from 'node:fs';

const [url = 'http://127.0.0.1:5230/', shots = ''] = process.argv.slice(2);
const exe = process.env.CHROME_PATH ?? (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined);
const browser = await chromium.launch({ executablePath: exe, headless: true });
const problems = [];
const consoleLines = [];
const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 }, acceptDownloads: true });
const page = await ctx.newPage();
page.on('requestfailed', (r) => { const u = new URL(r.url()); consoleLines.push(`requestfailed: ${u.origin}${u.pathname} ${r.failure()?.errorText}`); });
page.on('console', (m) => {
  consoleLines.push(`${m.type()}: ${m.text()}`);
  if (m.type() === 'error' || /Content Security Policy|Refused to/i.test(m.text())) problems.push(`console ${m.type()}: ${m.text()}`);
});
page.on('pageerror', (e) => problems.push(`pageerror: ${e.message}`));
await page.addInitScript(() => {
  document.addEventListener('securitypolicyviolation', (e) => {
    // eslint-disable-next-line no-console
    console.error(`CSP violation: ${e.violatedDirective} blocked=${e.blockedURI} sample=${e.sample}`);
  });
});
const res = await page.goto(url, { timeout: 120000 });
const h = res.headers();
for (const name of ['content-security-policy', 'referrer-policy', 'x-content-type-options', 'permissions-policy', 'cache-control']) {
  if (!h[name]) problems.push(`missing header ${name}`);
}
if (/unsafe-inline'[^;]*;/.test((h['content-security-policy'] ?? '').match(/script-src[^;]*;/)?.[0] ?? '')) problems.push('script-src allows unsafe-inline');
if (!/frame-ancestors 'none'/.test(h['content-security-policy'] ?? '')) problems.push('no frame-ancestors');

async function ask(text) {
  const box = page.getByRole('textbox', { name: 'Message' });
  await box.waitFor({ timeout: 180000 });
  await page.waitForFunction(() => { const t = document.querySelector('textarea[aria-label="Message"]'); return t && !t.disabled && !t.readOnly; }, null, { timeout: 180000 })
    .catch(async () => { await page.screenshot({ path: '/tmp/csp_stuck.png' }); throw new Error(`composer not editable before: ${text}`); });
  await box.fill(text);
  await page.keyboard.press('Enter');
  await page.waitForTimeout(300);
  await page.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 180000 });
  await page.waitForTimeout(1500);
}
const mock = process.env.CSP_MODE === 'mock';
try {
if (mock) {
  // Offline mock build: replay every fixture so each part type (code/Shiki, chart, image, artifact...) renders.
  const src = fs.readFileSync(new URL('../src/fixtures/index.ts', import.meta.url), 'utf8');
  const prompts = [...src.matchAll(/prompt: '((?:[^'\\]|\\.)*)'/g)].map((m) => m[1].replace(/\\'/g, "'"));
  for (const p of prompts) { await ask(p); }
} else {
  await ask('What is the denial rate by payer?');
  await page.locator('[data-testid="data-table"]').first().waitFor({ timeout: 120000 });
  await ask('Show the monthly trend of paid amount');
  await page.locator('[data-testid="chart"] svg').first().waitFor({ timeout: 120000 }).catch(() => problems.push('chart did not render'));
  await page.locator('[data-testid="chart"] svg path').first().hover().catch(() => undefined);
  await ask('list all claims');
}
} catch (e) { problems.push(`flow failed: ${String(e.message).split('\n')[0]}`); }
if (shots) { fs.mkdirSync(shots, { recursive: true }); await page.screenshot({ path: `${shots}/csp_check_${mock ? "mock" : "live"}.png`, fullPage: true }); }

// PHI: demo claim keys (AL…/AD…) and table cells must not leak to storage, URL or console.
const cells = await page.locator('[data-testid="data-table"] td').allInnerTexts();
// Prefer claim keys; otherwise any distinctive cell (live demo answers may only show aggregates).
const trimmed = [...new Set(cells.map((c) => c.trim()))];
const keys = trimmed.filter((c) => /^A[LD]\d{3,}/.test(c));
const sample = (keys.length ? keys : trimmed.filter((c) => c.length >= 5)).slice(0, 40);
if (!sample.length && !mock) problems.push('no table cells rendered (PHI check had nothing to look for)');
const storage = await page.evaluate(() => JSON.stringify({ l: { ...localStorage }, s: { ...sessionStorage } }));
for (const v of sample) {
  if (storage.includes(v)) problems.push(`row value ${v} found in web storage`);
  if (page.url().includes(v)) problems.push(`row value ${v} found in URL`);
  if (consoleLines.some((l) => l.includes(v))) problems.push(`row value ${v} found in console`);
}
await browser.close();
console.log(JSON.stringify({ url, checkedValues: sample.length, consoleLines: consoleLines.length, failed: consoleLines.filter((l) => l.startsWith('requestfailed')), problems }, null, 2));
process.exit(problems.length ? 1 : 0);
