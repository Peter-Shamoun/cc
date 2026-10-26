import { chromium } from 'playwright';
import fs from 'node:fs';
import { distRoutes, LIVE } from '../static.mjs';
const out = process.argv[2];
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome' });
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } }); await distRoutes(ctx);
const p = await ctx.newPage(); await p.goto(LIVE); await p.waitForTimeout(800);
const desc = () => p.evaluate(() => { const e = document.activeElement; if (!e || e === document.body) return 'BODY';
  const lm = e.closest('[role=dialog],[role=menu]');
  return `${e.tagName.toLowerCase()}${e.getAttribute('role') ? '[' + e.getAttribute('role') + ']' : ''} "${(e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 40)}"${lm ? ' in ' + lm.getAttribute('role') : ''} valuenow=${e.getAttribute('aria-valuenow') ?? ''} sort=${e.closest('th')?.getAttribute('aria-sort') ?? ''}`; });
const log = []; const L = async (l) => log.push(`${l}: ${await desc()}`);
const focusOn = async (loc) => { await loc.focus(); await L('focus'); };
await p.keyboard.type('Which CARC codes drive denials?'); await p.keyboard.press('Enter');
await p.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 }); await p.waitForTimeout(400);
// separator
await focusOn(p.getByRole('separator', { name: 'Resize column Claims' }));
await p.screenshot({ path: `${out}/kb-separator-focus.png`, clip: await p.locator('table').first().boundingBox().then((bb) => ({ x: bb.x - 10, y: bb.y - 10, width: Math.min(bb.width + 20, 800), height: 120 })) });
await p.keyboard.press('ArrowRight'); await p.keyboard.press('ArrowRight'); await L('sep ArrowRight x2');
await p.keyboard.press('ArrowLeft'); await L('sep ArrowLeft');
// sort
await focusOn(p.getByRole('button', { name: 'Claims', exact: true }));
await p.keyboard.press('Enter'); await p.waitForTimeout(150); await L('sort Enter');
await p.keyboard.press('Enter'); await p.waitForTimeout(150); await L('sort Enter2');
// table rows keyboard
await focusOn(p.getByLabel('Table rows'));
await p.keyboard.press('ArrowDown'); await L('rows ArrowDown');
// regenerate menu
await focusOn(p.getByRole('button', { name: 'More actions' }).last());
await p.keyboard.press('Enter'); await p.waitForTimeout(250); await L('more Enter');
await p.keyboard.press('ArrowDown'); await L('more ArrowDown');
await p.keyboard.press('ArrowDown'); await L('more ArrowDown2');
await p.screenshot({ path: `${out}/kb-more-menu.png` });
await p.keyboard.press('Escape'); await p.waitForTimeout(150); await L('more Esc');
const regen = p.getByRole('button', { name: 'Regenerate' }).last();
await focusOn(regen); await p.keyboard.press('Enter'); await p.waitForTimeout(250); await L('regen Enter');
await p.screenshot({ path: `${out}/kb-regen.png` });
if (await p.getByRole('menu').count()) { await p.keyboard.press('ArrowDown'); await L('regen ArrowDown'); await p.keyboard.press('Escape'); await p.waitForTimeout(150); await L('regen Esc'); }
// SQL inspector
await focusOn(p.getByRole('button', { name: 'Show SQL' }).last()); await p.keyboard.press('Enter'); await p.waitForTimeout(300); await L('sql Enter');
for (let i = 0; i < 12; i++) { await p.keyboard.press('Tab'); await L('sql Tab'); }
await p.keyboard.press('Escape'); await p.waitForTimeout(200); await L('sql Esc');
// expand table dialog
await focusOn(p.getByRole('button', { name: 'Expand table' }).last()); await p.keyboard.press('Enter'); await p.waitForTimeout(300); await L('expand Enter');
await p.keyboard.press('Escape'); await p.waitForTimeout(200); await L('expand Esc');
// edit via keyboard
await focusOn(p.getByRole('button', { name: 'Edit message' }).last()); await p.keyboard.press('Enter'); await p.waitForTimeout(250); await L('edit Enter');
await p.keyboard.press('Escape'); await p.waitForTimeout(200); await L('edit Esc');
// scope popover
await focusOn(p.getByRole('button', { name: /Claims lake/ })); await p.keyboard.press('Enter'); await p.waitForTimeout(250); await L('scope Enter');
await p.keyboard.press('ArrowDown'); await L('scope ArrowDown'); await p.keyboard.press('Escape'); await p.waitForTimeout(150); await L('scope Esc');
// slash
await p.getByRole('textbox', { name: 'Message' }).focus(); await p.keyboard.type('/'); await p.waitForTimeout(250); await L('slash');
await p.keyboard.press('ArrowDown'); await L('slash ArrowDown'); await p.screenshot({ path: `${out}/kb-slash.png` });
await p.keyboard.press('Escape'); await p.waitForTimeout(150); await L('slash Esc');
const menuVisible = await p.locator('[role=listbox]:visible,[role=menu]:visible').count(); log.push('menus visible after slash Esc: ' + menuVisible);
fs.writeFileSync(`${out}/kb_walk2.txt`, log.join('\n')); console.log(log.join('\n'));
await b.close();
