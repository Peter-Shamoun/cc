// QA headline-fidelity capture (10:48 PM directive): every screen/moment, for side-by-side grading vs Claude/ChatGPT.
// Usage (demo API on 8140): node e2e/review/fidelity3.mjs <outDir> [desktop-light,desktop-dark,mobile-light]
import { chromium } from 'playwright';
import fs from 'node:fs';
import { distRoutes, LIVE } from '../static.mjs';
const out = process.argv[2]; fs.mkdirSync(out, { recursive: true });
const want = (process.argv[3] ?? 'desktop-light,desktop-dark,mobile-light').split(',');
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome' });
const log = {};
for (const k of want) {
  const [dev, theme] = k.split('-'); const m = dev === 'mobile';
  const ctx = await b.newContext({ viewport: m ? { width: 390, height: 844 } : { width: 1440, height: 900 }, colorScheme: theme, isMobile: m, hasTouch: m, deviceScaleFactor: m ? 2 : 1 });
  await distRoutes(ctx); await ctx.grantPermissions(['clipboard-read', 'clipboard-write']);
  await ctx.addInitScript((t) => { try { localStorage.setItem('cc.theme', t); } catch {} }, theme);
  const p = await ctx.newPage(); const L = (log[k] = {});
  const tb = p.getByRole('textbox', { name: 'Message' }); const send = p.getByRole('button', { name: 'Send message' });
  const shot = async (n) => { await p.waitForTimeout(350); await p.screenshot({ path: `${out}/${k}-${n}.png` }); L[n] = 'ok'; };
  const ask = async (q, wait = true) => { await tb.fill(q); await tb.press('Enter'); if (wait) await send.waitFor({ timeout: 60000 }); };
  const step = async (n, fn) => { try { await fn(); } catch (e) { L[n] = 'ERR ' + e.message.split('\n')[0].slice(0, 120); } await p.keyboard.press('Escape').catch(() => {}); };
  const fresh = async () => { await p.goto(LIVE); await tb.waitFor(); };
  await fresh();
  await step('01-thinking', async () => { await ask('Show the paid amount trend slowly', false); await p.waitForTimeout(2200); await shot('01-thinking'); await send.waitFor({ timeout: 60000 }); await shot('02-chart-answer'); });
  await step('03-table', async () => { await ask('What was our denial rate by payer last quarter?'); await shot('03-table'); });
  await step('04-edit-mode', async () => { const u = p.getByTestId('msg-user').last(); await u.hover(); await p.getByRole('button', { name: 'Edit message' }).last().click(); await shot('04-edit-mode'); });
  await step('05-regenerate', async () => { await p.getByTestId('msg-assistant').last().hover(); await p.getByRole('button', { name: 'Regenerate' }).last().click(); await send.waitFor({ timeout: 60000 }); await p.getByTestId('msg-assistant').last().hover(); await shot('05-regenerated-pager'); });
  await step('06-feedback', async () => { await p.getByTestId('msg-assistant').last().hover(); await p.getByRole('button', { name: 'Bad response' }).last().click(); await shot('06-feedback'); });
  await step('07-more-menu', async () => { await p.getByTestId('msg-assistant').last().hover(); await p.getByRole('button', { name: 'More actions' }).last().click(); await shot('07-more-menu'); });
  await step('08-chat-options', async () => { if (m) { await p.getByRole('button', { name: 'Open sidebar' }).click(); await p.waitForTimeout(300); } await p.getByRole('button', { name: /^Chat options/ }).first().click(); await shot('08-chat-options'); });
  await step('10-search', async () => { if (m) { await p.getByRole('button', { name: 'Open sidebar' }).click(); await p.waitForTimeout(300); } await p.getByRole('button', { name: 'Search chats' }).first().click(); await tb.page().keyboard.type('denial'); await shot('10-search'); });
  await step('11-shortcuts', async () => { if (m) return; await p.keyboard.press('Control+/'); await shot('11-shortcuts'); });
  await step('12-account-menu', async () => { if (m) { await p.getByRole('button', { name: 'Open sidebar' }).click(); await p.waitForTimeout(300); } await p.getByRole('button', { name: /Demo user/ }).first().click(); await shot('12-account-menu'); });
  await step('13-settings', async () => { await p.getByRole('menuitem', { name: /Settings/ }).click().catch(async () => { if (m) { await p.getByRole('button', { name: 'Open sidebar' }).click(); await p.waitForTimeout(300); } await p.getByRole('button', { name: /Demo user/ }).first().click(); await p.getByRole('menuitem', { name: /Settings/ }).click(); }); await shot('13-settings'); });
  await step('14-sidebar-collapsed', async () => { if (m) { await p.getByRole('button', { name: 'Open sidebar' }).click(); await shot('14-mobile-drawer'); return; } await p.getByRole('button', { name: /Close sidebar|Collapse sidebar|Hide sidebar/ }).first().click(); await shot('14-sidebar-collapsed'); await p.getByRole('button', { name: /Open sidebar|Expand sidebar|Show sidebar/ }).first().click(); });
  await fresh();
  await step('15-clarify', async () => { await ask('How bad are denials?'); await shot('15-clarify'); });
  await fresh();
  await step('16-abstain', async () => { await ask('Forecast next quarter denials'); await shot('16-abstain'); });
  await fresh();
  await step('17-error', async () => { await ask('Simulate an error'); await shot('17-error'); });
  await fresh();
  await step('18-long-user', async () => { await ask('Please look at '.repeat(40) + 'the paid amount by payer, and also explain each step you take in detail so that I can follow along.'); await shot('18-long-user'); });
  await step('19-404', async () => { await p.goto(LIVE + 'definitely/not/here'); await p.waitForTimeout(800); await shot('19-404'); });
  await fresh();
  await step('20-temporary', async () => { await fresh(); const t = p.getByTestId('temporary-toggle').first(); await t.click(); await shot('20-temporary'); });
  await step('21-offline', async () => { await fresh(); await ctx.setOffline(true); await p.waitForTimeout(600); await tb.fill('hello'); await shot('21-offline'); await ctx.setOffline(false); });
  await ctx.close();
}
fs.writeFileSync(`${out}/fidelity3.json`, JSON.stringify(log, null, 1)); console.log(JSON.stringify(log, null, 1));
await b.close();
