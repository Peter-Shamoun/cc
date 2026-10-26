// Streaming parity probe: question pinned (no drift) while the answer streams, table rows arrive
// progressively, the timeline collapses when the answer starts, a reader who scrolls up mid-stream
// is not dragged, "Jump to latest" appears and, once clicked, follows the stream to the end.
// Usage: node scripts/e2e_answer_streaming.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer'] = process.argv.slice(2);
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const pg = await (await browser.newContext({ viewport: { width: 1024, height: 640 } })).newPage();
const errors = [];
pg.on('pageerror', (e) => errors.push(String(e)));
const box = () => pg.getByRole('textbox', { name: 'Message' });
const idle = () => pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 180000 });
const done = () => pg.getByRole('button', { name: 'Send message' }).isVisible().catch(() => false);
await pg.goto(base, { timeout: 90000 });
await box().waitFor({ timeout: 90000 });
const probe = () => pg.evaluate(() => {
  const sc = document.querySelector('[data-testid="thread-scroller"]');
  const us = [...document.querySelectorAll('[data-testid="msg-user"]')];
  const a = [...document.querySelectorAll('[data-testid="msg-assistant"]')].pop();
  const tl = a?.querySelector('[data-testid="step-timeline"]');
  const text = a?.querySelector('.prose-chat p, p');
  return {
    t: performance.now(), scrollTop: Math.round(sc.scrollTop), dist: Math.round(sc.scrollHeight - sc.scrollTop - sc.clientHeight),
    qTop: us.length ? Math.round(us[us.length - 1].getBoundingClientRect().top - sc.getBoundingClientRect().top) : null,
    rows: a ? Math.max(0, ...[...a.querySelectorAll('table')].map((x) => x.querySelectorAll('tbody tr').length)) : 0,
    live: tl?.dataset.live === 'true', hasText: !!text, jump: document.querySelector('[data-testid="jump-latest"]')?.getAttribute('aria-hidden') === 'false',
  };
});
// Run 1: pinned question, progressive rows, collapse timing.
await box().fill('Which CARC codes drive denials?');
await pg.keyboard.press('Enter');
const r1 = [];
while (!(await done())) { r1.push(await probe()); await pg.waitForTimeout(60); }
r1.push(await probe());
const qTops = r1.filter((s) => s.qTop !== null).map((s) => s.qTop);
const settled = qTops.slice(Math.min(qTops.length - 1, 10));
const rowSteps = [...new Set(r1.map((s) => s.rows))].filter((n) => n > 0);
const firstText = r1.find((s) => s.hasText);
const collapsedAt = r1.find((s) => firstText && s.t >= firstText.t && !s.live);
await pg.screenshot({ path: `${out}/d4_01_streamed_pinned.png` });
// Run 2: scroll up mid-stream, then jump to latest.
await box().fill('Denial rate by payer');
await pg.keyboard.press('Enter');
await pg.locator('[data-testid="msg-assistant"]').nth(1).waitFor();
await pg.waitForTimeout(450); // wheel while the pin-to-top smooth scroll may still be running
await pg.mouse.move(600, 300);
await pg.mouse.wheel(0, -600);
await pg.waitForTimeout(250);
const before = await probe();
const drift = [];
let jumpShown = false;
for (let i = 0; i < 40 && !(await done()) && !jumpShown; i++) { await pg.waitForTimeout(150); const s = await probe(); drift.push(s.scrollTop - before.scrollTop); jumpShown = s.jump; }
await pg.screenshot({ path: `${out}/d4_02_scrolled_up_jump.png` });
let followed = null;
if (jumpShown) {
  await pg.getByTestId('jump-latest').click();
  const dists = [];
  while (!(await done())) { await pg.waitForTimeout(120); dists.push((await probe()).dist); }
  await pg.waitForTimeout(400);
  dists.push((await probe()).dist);
  followed = { maxDist: dists.length > 4 ? Math.max(...dists.slice(3)) : null, end: dists.at(-1), streamingAfterJump: dists.length > 1 };
}
await idle();
await pg.screenshot({ path: `${out}/d4_03_followed_to_end.png` });
// Run 3: scroll far up right away, jump back while still streaming, and check it follows to the end.
await box().fill('Which CARC codes drive denials?');
await pg.keyboard.press('Enter');
await pg.locator('[data-testid="msg-assistant"]').nth(2).waitFor();
await pg.waitForTimeout(1200);
await pg.mouse.move(600, 300);
await pg.mouse.wheel(0, -3000);
await pg.waitForTimeout(400);
let live3 = null;
if (!(await done()) && (await probe()).jump) {
  await pg.getByTestId('jump-latest').click();
  const d = [];
  while (!(await done())) { await pg.waitForTimeout(100); d.push((await probe()).dist); }
  await pg.waitForTimeout(400);
  d.push((await probe()).dist);
  live3 = { samples: d.length, maxDistAfterSettle: Math.max(...d.slice(Math.max(0, d.findIndex((x) => x < 80)))), end: d.at(-1) };
}
const res = {
  samples: r1.length, qTopRange: [Math.min(...settled), Math.max(...settled)], rowSteps,
  collapseLagMs: firstText && collapsedAt ? Math.round(collapsedAt.t - firstText.t) : null,
  readerDriftPx: Math.max(0, ...drift.map(Math.abs)), jumpShown, followed, followWhileStreaming: live3, errors,
};
console.log(JSON.stringify(res));
await browser.close();
process.exit(res.qTopRange[1] - res.qTopRange[0] <= 2 && res.rowSteps.length > 1 && res.readerDriftPx <= 2 && res.jumpShown && res.followed && res.followed.end < 80 && res.followWhileStreaming && res.followWhileStreaming.end < 80 && res.followWhileStreaming.maxDistAfterSettle < 120 && !errors.length ? 0 : 1);
