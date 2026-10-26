import { chromium } from 'playwright';
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome' });
const pg = await (await b.newContext({ viewport: { width: 1024, height: 800 } })).newPage();
await pg.goto('http://127.0.0.1:5211/');
const box = pg.getByRole('textbox', { name: 'Message' });
for (const q of ['What was our denial rate by payer last quarter?', 'Who are our top providers?']) {
  await box.fill(q); await pg.keyboard.press('Enter');
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 });
}
// Menu item exists, and invoking it calls print with only the first Q+A marked.
await pg.evaluate(() => { window.__seen = []; window.print = () => { window.__seen.push([...document.querySelectorAll('[data-print-keep]')].map((e) => e.querySelector('[data-testid]')?.getAttribute('data-testid'))); }; });
const first = pg.getByTestId('msg-assistant').first();
await first.hover();
await first.getByTestId('more-actions').click();
await pg.getByRole('menuitem', { name: 'Print this answer' }).click();
console.log(JSON.stringify(await pg.evaluate(() => window.__seen)));
// Re-mark (as during the dialog) and render print media to PDF.
await pg.evaluate(() => { const row = document.querySelector('[data-testid="msg-assistant"]').closest('[data-message-id]'); row.setAttribute('data-print-keep', ''); row.previousElementSibling.setAttribute('data-print-keep', ''); document.body.setAttribute('data-print-one', ''); });
await pg.pdf({ path: '/workspace/cc_fe/shots/answer/e4/print_one.pdf', format: 'A4' });
await b.close();
