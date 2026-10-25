// v1 screenshots. Live round-trip against api/ (demo mode) + v1 features on the mock adapter.
// Usage: node scripts/shots_v1.mjs <liveUrl> <mockUrl> <outDir>
//   liveUrl: vite started with VITE_ANALYST_BASE=/ VITE_ANALYST_DEMO_TOKEN=demo:acme_labs:<user>, api on :8787
import { chromium } from 'playwright';
import fs from 'node:fs';

const [live = 'http://127.0.0.1:5174/', mock = 'http://127.0.0.1:5175/', out = '/workspace/cc_web/shots'] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const exe = process.env.CHROME_PATH ?? (fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined);
const browser = await chromium.launch({ executablePath: exe, headless: true });
const consoleLines = [];
const network = [];

async function page(base, { palette = 'neutral', theme = 'light', w = 1440, h = 900, mobile = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: mobile ? 2 : 1, isMobile: mobile, hasTouch: mobile });
  await ctx.addInitScript(([p, t]) => { localStorage.setItem('cc.pref.palette', p); localStorage.setItem('cc.pref.theme', t); }, [palette, theme]);
  const pg = await ctx.newPage();
  pg.on('console', (m) => consoleLines.push(m.text()));
  pg.on('request', (r) => network.push(`${r.method()} ${new URL(r.url()).pathname}`));
  await pg.goto(base);
  return pg;
}
async function ask(pg, prompt) {
  await pg.getByRole('textbox', { name: 'Message' }).fill(prompt);
  await pg.keyboard.press('Enter');
  await pg.waitForTimeout(300);
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 });
  await pg.waitForTimeout(1500);
}
const shot = (pg, name, full = false) => pg.screenshot({ path: `${out}/v1_${name}.png`, fullPage: full });

// 1. Live round-trip: browser -> vite proxy -> FastAPI /api/chat SSE -> analyzer (scripted, offline) -> DuckDB demo data.
let pg = await page(live);
await shot(pg, '01_live_empty');
await ask(pg, 'What is the denial rate by payer?');
await pg.locator('[data-testid="data-table"]').first().waitFor();
await shot(pg, '02_live_denial_rate_by_payer');
const dl = pg.waitForEvent('download', { timeout: 10000 }).catch(() => null);
await pg.locator('[data-testid="data-table"]').first().hover();
await pg.getByLabel('Download CSV').first().click();
const d = await dl;
const csvHead = d ? fs.readFileSync(await d.path(), 'utf8').split('\n')[0] : 'no download';
await ask(pg, 'Show the monthly trend of paid amount');
await shot(pg, '03_live_monthly_trend_chart');
await ask(pg, 'List the patient names and member IDs for denied claims');
await shot(pg, '04_live_refusal_followups');
await pg.setViewportSize({ width: 1440, height: 900 });
await shot(pg, '05_live_full_thread', true);

// 2. Paper theme, live
pg = await page(live, { palette: 'paper' });
await ask(pg, 'Total paid by payer');
await shot(pg, '06_live_paper_paid_by_payer');

// 3. Edit-and-branch (mock)
pg = await page(mock);
await ask(pg, 'What was our denial rate by payer last quarter?');
await pg.getByTestId('msg-user').first().hover();
await pg.getByRole('button', { name: 'Edit message' }).first().click();
await pg.getByRole('textbox', { name: 'Edit message' }).fill('What was our denial rate by payer last month?');
await shot(pg, '07_edit_inline');
await pg.getByRole('button', { name: 'Send', exact: true }).click();
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 30000 });
await pg.waitForTimeout(1200);
await pg.evaluate(() => document.querySelector('main .overflow-y-auto')?.scrollTo(0, 0));
await shot(pg, '08_branch_version_2of2');
await pg.getByRole('button', { name: 'Previous version' }).click();
await pg.waitForTimeout(600);
await pg.evaluate(() => document.querySelector('main .overflow-y-auto')?.scrollTo(0, 0));
await shot(pg, '09_branch_version_1of2');

// 3b. Thumbs-down feedback panel (mock)
await pg.getByRole('button', { name: 'Bad response' }).last().click();
await pg.getByRole('button', { name: 'Wrong numbers' }).click();
await pg.waitForTimeout(300);
await shot(pg, '16_feedback_panel');
await pg.getByRole('button', { name: 'Close feedback' }).click();

// 4. Search + rename (mock)
await ask(pg, 'Plot the monthly denial rate trend for our top 3 payers');
await pg.getByRole('button', { name: 'New chat' }).first().click();
await ask(pg, 'Which CARC codes drive Cascade Medicaid denials?');
await pg.getByRole('button', { name: 'Rename chat' }).first().click({ force: true });
await pg.getByRole('textbox', { name: 'Rename chat' }).fill('Cascade Medicaid CARC drivers');
await shot(pg, '10_sidebar_rename');
await pg.keyboard.press('Enter');
await pg.keyboard.press('Control+k');
await pg.getByRole('textbox', { name: 'Search chats' }).fill('cascade');
await shot(pg, '11_sidebar_search');

// 5. Large table, virtualized in the artifact panel (mock fixture with rowCount > 50)
await pg.getByRole('textbox', { name: 'Search chats' }).fill('');
await pg.locator('[data-testid="data-table"]').last().hover();
await pg.getByLabel('Expand table').last().click();
await pg.waitForTimeout(600);
await shot(pg, '12_artifact_table');

// 6. Mobile drawer (mock)
pg = await page(mock, { w: 390, h: 844, mobile: true });
await ask(pg, 'What was our denial rate by payer last quarter?');
await shot(pg, '13_mobile_thread');
await pg.getByRole('button', { name: 'Open sidebar' }).click();
await pg.waitForTimeout(400);
await shot(pg, '14_mobile_drawer');

// 7. Dark, Neutral, live
pg = await page(live, { theme: 'dark' });
await ask(pg, 'What is the denial rate by payer?');
await shot(pg, '15_live_dark');

await browser.close();
const apiCalls = network.filter((n) => n.includes('/api/'));
fs.writeFileSync(`${out}/v1_run.json`, JSON.stringify({ csvHead, apiCalls: [...new Set(apiCalls)], consoleLines }, null, 2));
console.log(JSON.stringify({ csvHead, apiCalls: [...new Set(apiCalls)], consoleCount: consoleLines.length, console: consoleLines.slice(0, 10) }, null, 2));
