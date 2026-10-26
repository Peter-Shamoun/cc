// QA-A15/A16/A17 probe: one "Thinking…" row from the first frame after send (live + mock), the row
// element survives step arrival, "Thought for" ≥ longest step, and "Jump to latest" leaves content clear.
// Usage: node scripts/e2e_answer_thinking.mjs <baseUrl> <tag> [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', tag = 'live', out = '/workspace/cc_fe/shots/answer/e1'] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const pg = await (await browser.newContext({ viewport: { width: 1024, height: 640 } })).newPage();
const errors = [];
pg.on('pageerror', (e) => errors.push(String(e)));
const box = () => pg.getByRole('textbox', { name: 'Message' });
const done = () => pg.getByRole('button', { name: 'Send message' }).isVisible().catch(() => false);
await pg.goto(base, { timeout: 90000 });
await box().waitFor({ timeout: 90000 });
await pg.evaluate(() => {
  window.__rows = [];
  const tick = () => {
    const a = [...document.querySelectorAll('[data-testid="msg-assistant"]')].pop();
    if (a) {
      const s = a.querySelector('[data-testid="step-summary"]');
      if (s && !s.__id) s.__id = Math.random();
      window.__rows.push({ caret: !!a.querySelector('.streaming-caret'), summary: s?.textContent ?? null, id: s?.__id ?? null, icon: !!s?.querySelector('svg') });
    }
    requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
});
await box().fill('Which CARC codes drive denials?');
await pg.keyboard.press('Enter');
await pg.waitForTimeout(120);
await pg.screenshot({ path: `${out}/${tag}_01_first_frames.png` });
while (!(await done())) await pg.waitForTimeout(100);
const rows = await pg.evaluate(() => window.__rows);
const ids = new Set(rows.map((r) => r.id).filter(Boolean));
const firstSummary = rows.find((r) => r.summary);
const result = {
  frames: rows.length, framesBeforeRow: rows.findIndex((r) => r.summary), caretFrames: rows.filter((r) => r.caret).length,
  iconless: rows.filter((r) => r.summary && !r.icon).length, rowElements: ids.size, first: firstSummary?.summary, last: rows.at(-1)?.summary,
};
// A16: Thought for vs longest step.
const sum = pg.locator('[data-testid="msg-assistant"]').last().getByTestId('step-summary');
await sum.click();
const stepMs = await pg.locator('[data-testid="msg-assistant"]').last().getByTestId('step').allTextContents();
result.thought = (await sum.textContent())?.match(/Thought for ([\d.]+)(ms|s)/)?.slice(1).join('');
result.stepTimes = stepMs.map((t) => t.match(/([\d.]+)\s?(ms|s)\b/)?.slice(1).join('')).filter(Boolean);
await pg.screenshot({ path: `${out}/${tag}_02_thought_vs_steps.png` });
// A17: scroll up into the chart/table; the jump button must sit over the composer edge, not content.
const sc = pg.getByTestId('thread-scroller');
await sc.evaluate((el) => el.scrollTo({ top: el.scrollHeight - el.clientHeight - 260 }));
await pg.waitForTimeout(400);
const geo = await pg.evaluate(() => {
  const j = document.querySelector('[data-testid="jump-latest"]').getBoundingClientRect();
  const comp = document.querySelector('#composer form, #composer [data-testid="composer"], #composer > div:not([aria-hidden])')?.getBoundingClientRect();
  const sc = document.querySelector('[data-testid="thread-scroller"]').getBoundingClientRect();
  return { jumpTop: Math.round(j.top), jumpBottom: Math.round(j.bottom), composerTop: comp ? Math.round(comp.top) : null, scrollerBottom: Math.round(sc.bottom) };
});
result.jump = { ...geo, overContentPx: Math.max(0, geo.scrollerBottom - 24 - geo.jumpTop) };
await pg.screenshot({ path: `${out}/${tag}_03_jump_clear.png` });
console.log(JSON.stringify({ tag, ...result, errors }, null, 1));
await browser.close();
