// QA-A2/A12/P5 e2e: after the answer is idle the "Jump to latest" arrow shows when the chart sits
// below the fold; the chart is fully drawn (no placeholder); many/long payer labels read as
// horizontal bars (no rotated labels); acronyms stay uppercase in chart titles.
// Usage: node scripts/e2e_answer_charts.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const pg = await (await browser.newContext({ viewport: { width: 1024, height: 640 } })).newPage();
const errors = [];
pg.on('pageerror', (e) => errors.push(String(e)));
const box = () => pg.getByRole('textbox', { name: 'Message' });
const ask = async (q) => {
  await box().fill(q);
  await pg.keyboard.press('Enter');
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
  await pg.waitForTimeout(900);
};
const chartInfo = () => pg.evaluate(() => {
  const fig = [...document.querySelectorAll('[data-testid="chart"]')].pop();
  if (!fig) return null;
  const texts = [...fig.querySelectorAll('svg text')];
  const rotated = texts.filter((t) => /rotate\((-?90|270)/.test(t.getAttribute('transform') ?? '')).map((t) => t.textContent);
  return {
    title: fig.querySelector('figcaption')?.textContent,
    skeleton: !!fig.querySelector('[data-testid="chart-skeleton"]'),
    marks: fig.querySelectorAll('svg path[aria-roledescription="bar"], svg .mark-rect path').length,
    rotatedLabels: rotated.filter((t) => t && !/rate|claims|name|code/i.test(t)),
  };
});
const jumpVisible = () => pg.getByTestId('jump-latest').evaluate((b) => getComputedStyle(b).opacity === '1' && b.getAttribute('aria-hidden') !== 'true');
await pg.goto(base, { timeout: 90000 });
await box().waitFor({ timeout: 90000 });
await ask('What is the denial rate by payer?');
const r1 = { jumpWhenIdle: await jumpVisible(), chart: await chartInfo() };
await pg.screenshot({ path: `${out}/c3_01_idle_jump_latest.png` });
await pg.getByTestId('jump-latest').click();
await pg.waitForTimeout(700);
r1.jumpAfterClick = await jumpVisible();
await pg.screenshot({ path: `${out}/c3_02_payer_chart_horizontal.png` });
await ask('Which CARC codes drive denials?');
await pg.getByTestId('jump-latest').click().catch(() => {});
await pg.waitForTimeout(700);
const r2 = { chart: await chartInfo() };
await pg.screenshot({ path: `${out}/c3_03_carc_chart_title.png` });
console.log(JSON.stringify({ payer: r1, carc: r2, pageErrors: errors }, null, 1));
await browser.close();
