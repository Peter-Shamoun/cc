// Contract 1.7.0 feedback, live: thumbs-down + reason chip + comment → PUT …/feedback; the server
// echoes it on reload (StoredMessage.feedback → pressed thumb); un-voting sends DELETE.
// Usage: node scripts/e2e_answer_feedback.mjs [baseUrl] [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const [base = 'http://127.0.0.1:5211/', out = '/workspace/cc_fe/shots/answer/e1'] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const exe = fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined;
const browser = await chromium.launch({ executablePath: exe, headless: true });
const pg = await (await browser.newContext({ viewport: { width: 1024, height: 700 } })).newPage();
const errors = [], reqs = [];
pg.on('pageerror', (e) => errors.push(String(e)));
pg.on('response', (r) => { if (r.url().includes('/feedback')) reqs.push({ method: r.request().method(), status: r.status(), path: new URL(r.url()).pathname.replace(/[^/]+\/messages\/[^/]+/, '{c}/messages/{m}'), body: r.request().postDataJSON?.() ?? null }); });
const box = () => pg.getByRole('textbox', { name: 'Message' });
await pg.goto(base, { timeout: 90000 });
await box().waitFor({ timeout: 90000 });
await box().fill('What is the denial rate by payer?');
await pg.keyboard.press('Enter');
await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 120000 });
await pg.getByRole('button', { name: 'Bad response' }).last().click();
const chips = await pg.getByTestId('feedback-panel').locator('button[data-reason]').evaluateAll((b) => b.map((x) => `${x.dataset.reason}=${x.textContent}`));
await pg.getByRole('button', { name: 'Numbers look wrong' }).click();
await pg.getByRole('textbox', { name: 'Feedback detail' }).fill('Synthetic test note');
await pg.screenshot({ path: `${out}/fb_01_panel.png` });
await pg.getByRole('button', { name: 'Submit' }).click();
await pg.waitForTimeout(600);
const url = pg.url();
await pg.reload();
await pg.getByRole('button', { name: 'Bad response' }).last().waitFor({ timeout: 30000 });
const pressedAfterReload = await pg.getByRole('button', { name: 'Bad response' }).last().getAttribute('aria-pressed');
await pg.screenshot({ path: `${out}/fb_02_after_reload.png` });
await pg.getByRole('button', { name: 'Bad response' }).last().click(); // un-vote → DELETE
await pg.waitForTimeout(600);
console.log(JSON.stringify({ chips, reqs, urlHasNote: url.includes('Synthetic'), pressedAfterReload, errors }, null, 1));
await browser.close();
