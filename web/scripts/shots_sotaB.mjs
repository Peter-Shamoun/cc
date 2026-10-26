// Team B (answer surface) screenshots against the mock adapter.
// Usage: node scripts/shots_sotaB.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';

const [base = 'http://127.0.0.1:5202/', out = '/workspace/cc_web/shots'] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const exe = process.env.CHROME_PATH ?? (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined);
const browser = await chromium.launch({ executablePath: exe, headless: true });
const errors = [];

async function page({ palette = 'neutral', theme = 'light', w = 1280, h = 900, mobile = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: mobile ? 2 : 1, isMobile: mobile, hasTouch: mobile });
  await ctx.addInitScript(([p, t]) => { localStorage.setItem('cc.pref.palette', p); localStorage.setItem('cc.pref.theme', t); }, [palette, theme]);
  const pg = await ctx.newPage();
  pg.on('pageerror', (e) => errors.push(String(e)));
  await pg.goto(base);
  return pg;
}
async function ask(pg, prompt, { wait = true } = {}) {
  await pg.getByRole('textbox', { name: 'Message' }).fill(prompt);
  await pg.keyboard.press('Enter');
  if (!wait) return;
  await pg.waitForTimeout(300);
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 });
  await pg.waitForTimeout(900);
}
const shot = (pg, name, full = false) => pg.screenshot({ path: `${out}/sota_B_${name}.png`, fullPage: full });
const DUP = 'Which providers have the highest duplicate-claim denial rate?';

// 1. live timeline mid-stream
let pg = await page();
await ask(pg, DUP, { wait: false });
await pg.locator('[data-testid="step"][data-kind="repair"]').waitFor({ timeout: 10000 });
await pg.waitForTimeout(150);
await shot(pg, '01_timeline_live');
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 });
await pg.waitForTimeout(900);
await shot(pg, '02_answer_collapsed');
// 3. expanded timeline
await pg.getByTestId('step-summary').click();
await pg.waitForTimeout(400);
await shot(pg, '03_timeline_expanded_repair_candidates', true);
await pg.getByTestId('step-summary').click();
// 4. SQL under table + inspector + meta popover
await pg.getByTestId('table-sql').getByRole('button').click();
await pg.getByTestId('open-sql').click();
await pg.getByTestId('meta-line').click();
await pg.waitForTimeout(500);
await shot(pg, '04_sql_inspector_meta', true);

// 5. dark: escalation + expanded
pg = await page({ theme: 'dark' });
await ask(pg, DUP);
// escalation pill appears once the mock replays v1 `escalation` events
if (await pg.getByTestId('escalation').count()) await pg.getByTestId('escalation').getByRole('button').click();
await pg.getByTestId('step-summary').click();
await pg.waitForTimeout(400);
await shot(pg, '05_dark_escalation_steps', true);

// 6. abstain
pg = await page();
await ask(pg, 'What is our patient satisfaction score by clinic?');
await shot(pg, '06_abstain');

// 7-8. clarify then answered
pg = await page({ palette: 'paper' });
await ask(pg, 'Who are our top providers?');
await shot(pg, '07_clarify_question');
await pg.getByTestId('clarify-chip').nth(1).click();
await pg.waitForTimeout(300);
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 });
await pg.waitForTimeout(900);
await shot(pg, '08_clarify_resumed', true);

// 9. cancel mid-run
pg = await page({ palette: 'paper', theme: 'dark' });
await ask(pg, DUP, { wait: false });
await pg.locator('[data-testid="step"][data-kind="sql"]').waitFor({ timeout: 10000 });
await pg.getByRole('button', { name: /Stop/ }).click();
await pg.waitForTimeout(800);
await shot(pg, '09_stopped_retry');

// 10. error state
pg = await page();
await ask(pg, 'Run the query against the raw 837 tables');
await shot(pg, '10_error');

// 11. mobile
pg = await page({ w: 390, h: 844, mobile: true });
await ask(pg, DUP);
await pg.getByTestId('meta-line').click();
await pg.waitForTimeout(300);
await shot(pg, '11_mobile_answer', true);

await browser.close();
if (errors.length) { console.error('page errors:', errors); process.exit(1); }
console.log('ok');
