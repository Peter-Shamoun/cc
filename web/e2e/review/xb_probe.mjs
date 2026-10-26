// QA cross-browser probe: paste, focus after send, find in virtualized shadows. node e2e/review/xb_probe.mjs firefox|webkit
import * as pw from 'playwright';
import { distRoutes, LIVE, MOCK } from '../static.mjs';
const b = await pw[process.argv[2] || 'firefox'].launch();
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } }); await distRoutes(ctx);
const p = await ctx.newPage();
const st = () => p.evaluate(() => ({ active: document.activeElement?.tagName + ':' + (document.activeElement?.getAttribute('aria-label') || ''), hasFocus: document.hasFocus() }));
await p.goto(MOCK); await p.waitForTimeout(800);
console.log('load', await st());
const box = p.getByRole('textbox', { name: 'Message' });
// real copy/paste through the browser clipboard
await p.evaluate(() => { const t = document.createElement('textarea'); t.id = 'qa-src'; t.value = Array.from({ length: 30 }, (_, i) => `row ${i},CO-45,denied`).join('\n'); document.body.append(t); });
await p.locator('#qa-src').focus(); await p.keyboard.press('ControlOrMeta+a'); await p.keyboard.press('ControlOrMeta+c');
await p.evaluate(() => document.getElementById('qa-src').remove());
await box.focus(); await p.keyboard.press('ControlOrMeta+v'); await p.waitForTimeout(300);
console.log('real paste chips', await p.getByTestId('paste-chips').getByRole('listitem').count(), 'textarea len', (await box.inputValue()).length);
await box.fill('');
const synth = await box.evaluate((el) => { const dt = new DataTransfer(); dt.setData('text/plain', 'x\n'.repeat(40)); const ev = new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }); const got = ev.clipboardData?.getData('text/plain')?.length; el.dispatchEvent(ev); return { got }; });
console.log('synthetic clipboardData len', synth);
await p.reload(); await p.waitForTimeout(800);
// focus through first send
await box.fill('Total paid by payer'); await box.press('Enter');
for (const t of [100, 500, 1500, 4000]) { await p.waitForTimeout(t); console.log('after send +', t, await st()); }
await b.close();
