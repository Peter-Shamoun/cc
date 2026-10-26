// QA deep fidelity pass (C9 follow-up): message hover rows, copy feedback, sidebar row states, composer buttons,
// picker placement, empty state, scroll-to-bottom, code block header, stop button. Light+dark, 1440 + 390.
// Usage (demo API on 8140): node e2e/review/fidelity2.mjs <outDir>
import { chromium } from 'playwright';
import fs from 'node:fs';
import { distRoutes, LIVE } from '../static.mjs';
const out = process.argv[2]; fs.mkdirSync(out, { recursive: true });
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome' });
const R = {};
const box = (p, loc) => loc.evaluate((e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return { x: Math.round(r.x), y: Math.round(r.y), w: Math.round(r.width), h: Math.round(r.height), radius: s.borderRadius, bg: s.backgroundColor, color: s.color, border: s.borderTopWidth + ' ' + s.borderTopColor, shadow: s.boxShadow === 'none' ? 'none' : s.boxShadow.slice(0, 60), opacity: s.opacity, font: `${s.fontSize}/${s.fontWeight}` }; }).catch((e) => 'n/a ' + e.message.slice(0, 50));
const codeSse = () => {
  const id = 'm_code_' + Math.random().toString(36).slice(2, 7);
  const md = 'Here is the query:\n\n```sql\nSELECT payer_name, COUNT(*) AS claims\nFROM sem.claims\nGROUP BY payer_name\nORDER BY claims DESC;\n```\n\nAnd in Python:\n\n```python\nimport pandas as pd\ndf = pd.read_sql(q, conn)\n```\n';
  return [{ type: 'message.start', messageId: id, conversationId: 'c_code' }, { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: md }, { type: 'text.end', partId: 't' }, { type: 'message.end', messageId: id, finishReason: 'stop', usage: { queries: 0, elapsedMs: 10 } }].map((e, i) => `id: ${i}\ndata: ${JSON.stringify(e)}\n\n`).join('');
};
for (const [vp, tag] of [[{ width: 1440, height: 900 }, 'desktop'], [{ width: 390, height: 844 }, 'mobile']]) for (const theme of ['light', 'dark']) {
  const m = vp.width < 500; const k = `${tag}-${theme}`; const r = (R[k] = {});
  const ctx = await b.newContext({ viewport: vp, colorScheme: theme, isMobile: m, hasTouch: m, deviceScaleFactor: m ? 2 : 1 });
  await distRoutes(ctx); await ctx.grantPermissions(['clipboard-read', 'clipboard-write']); await ctx.addInitScript((t) => { try { localStorage.setItem('cc.theme', t); } catch {} }, theme);
  const p = await ctx.newPage(); await p.goto(LIVE);
  const tb = p.getByRole('textbox', { name: 'Message' }); await tb.waitFor();
  const send = p.getByRole('button', { name: 'Send message' });
  // Empty state
  r.empty = await p.evaluate(() => { const h = document.querySelector('h1'); const hr = h?.getBoundingClientRect(); const ta = document.querySelector('textarea')?.closest('.cc-composer') ?? document.querySelector('textarea'); const cr = ta?.getBoundingClientRect();
    return { heading: h?.textContent?.trim(), size: h && getComputedStyle(h).fontSize, family: h && getComputedStyle(h).fontFamily.split(',')[0], headingCenterY: hr && +((hr.y + hr.height / 2) / innerHeight).toFixed(2), composerY: cr && +((cr.y) / innerHeight).toFixed(2), composerW: cr && Math.round(cr.width), chips: [...document.querySelectorAll('[data-testid*=starter] button, [data-testid*=starter] a')].map((e) => e.textContent.trim().slice(0, 30)) }; });
  await p.screenshot({ path: `${out}/${k}-empty.png` });
  // Composer buttons, empty vs typed
  r.attach = await box(p, p.getByRole('button', { name: /attach/i }).first());
  r.sendDisabled = await box(p, send);
  await tb.fill('x'); r.sendEnabled = await box(p, send); await tb.fill('');
  // Picker placement
  r.picker = await p.evaluate(() => { const c = [...document.querySelectorAll('button[aria-haspopup]')].find((e) => /Analyst|ClaimClarity|strategy|model/i.test(e.textContent + (e.getAttribute('aria-label') || ''))); if (!c) return null; const rr = c.getBoundingClientRect(); return { text: c.textContent.trim().slice(0, 40), label: c.getAttribute('aria-label'), x: Math.round(rr.x), y: Math.round(rr.y), inComposer: !!c.closest('.cc-composer, form'), inHeader: !!c.closest('header') }; });
  // Stop button mid-stream (slow scenario)
  await tb.fill('Show the paid amount trend slowly'); await tb.press('Enter');
  const stop = p.getByRole('button', { name: /^stop/i }); await stop.waitFor({ timeout: 10000 }).catch(() => {});
  r.stop = await box(p, stop);
  r.stopIcon = await stop.evaluate((e) => { const s = e.querySelector('svg'); return s ? { cls: s.getAttribute('class'), w: s.getAttribute('width'), fill: getComputedStyle(s).fill, rect: !!s.querySelector('rect') } : null; }).catch(() => null);
  await p.screenshot({ path: `${out}/${k}-stop.png` });
  await send.waitFor({ timeout: 60000 });
  // Assistant hover action row
  const last = p.getByTestId('msg-assistant').last();
  const actionsInfo = async () => last.evaluate((e) => [...e.querySelectorAll('button')].filter((b) => { const r = b.getBoundingClientRect(); return r.width && r.height && b.closest('[data-testid*=actions], [role=toolbar]'); }).map((b) => { const r = b.getBoundingClientRect(); const s = getComputedStyle(b); const svg = b.querySelector('svg'); let o = 1, el = b; while (el) { o *= +getComputedStyle(el).opacity; el = el.parentElement; } return `${b.getAttribute('aria-label') || b.textContent.trim().slice(0, 16)} ${Math.round(r.width)}x${Math.round(r.height)} r=${s.borderRadius} icon=${svg?.getAttribute('width')} op=${o.toFixed(2)}`; }));
  await p.mouse.move(5, vp.height - 5);
  r.assistantActionsIdle = await actionsInfo();
  await last.hover(); await p.waitForTimeout(250);
  r.assistantActionsHover = await actionsInfo();
  r.toolbar = await last.evaluate((e) => { const t = e.querySelector('[data-testid*=actions], [role=toolbar]'); if (!t) return null; const rr = t.getBoundingClientRect(); return { testid: t.getAttribute('data-testid'), role: t.getAttribute('role'), gap: getComputedStyle(t).gap, y: Math.round(rr.y), h: Math.round(rr.height) }; });
  await p.screenshot({ path: `${out}/${k}-assistant-hover.png` });
  // Copy feedback
  const copyBtn = last.getByRole('button', { name: /^copy/i }).first();
  if (await copyBtn.count()) { await copyBtn.click(); await p.waitForTimeout(150);
    r.copyFeedback = await p.evaluate(() => ({ toasts: [...document.querySelectorAll('[data-testid*=toast], [role=status]')].map((t) => t.textContent.trim()).filter(Boolean).slice(0, 4), focusedLabel: document.activeElement?.getAttribute('aria-label') }));
    r.copyLabelAfter = await copyBtn.getAttribute('aria-label').catch(() => null);
    await p.waitForTimeout(2500); r.copyLabelAfter2_5s = await last.getByRole('button', { name: /^cop/i }).first().getAttribute('aria-label').catch(() => null);
    await p.screenshot({ path: `${out}/${k}-copied.png` }); }
  // Keyboard Ctrl+Shift+C toast
  if (!m) { await p.keyboard.press('Control+Shift+C'); await p.waitForTimeout(300); r.copyShortcutToast = await p.evaluate(() => [...document.querySelectorAll('[data-testid*=toast]')].map((t) => t.textContent.trim()).slice(0, 2)); }
  // User message hover
  const user = p.getByTestId('msg-user').last(); await user.hover(); await p.waitForTimeout(250);
  r.userHover = await user.evaluate((e) => { const root = e.closest('[data-testid=turn], li, article') ?? e.parentElement; return [...root.querySelectorAll('button')].filter((b) => b.getBoundingClientRect().width).map((b) => `${b.getAttribute('aria-label') || b.textContent.trim().slice(0, 12)} ${Math.round(b.getBoundingClientRect().width)}px`); });
  await p.screenshot({ path: `${out}/${k}-user-hover.png` });
  // Code block header (injected)
  await p.route('**/api/chat', (rt) => rt.request().method() === 'POST' ? rt.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: codeSse() }) : rt.continue());
  await tb.fill('code please'); await tb.press('Enter'); await send.waitFor({ timeout: 20000 }); await p.waitForTimeout(500);
  r.code = await p.evaluate(() => { const pre = [...document.querySelectorAll('pre')].pop(); if (!pre) return null; const wrap = pre.closest('[data-testid*=code], figure, div'); const hdr = wrap?.querySelector('[data-testid*=code-header], header, div'); const wr = wrap.getBoundingClientRect(); const ws = getComputedStyle(wrap);
    return { wrapRadius: ws.borderRadius, wrapBorder: ws.borderTopWidth + ' ' + ws.borderTopColor, wrapBg: ws.backgroundColor, w: Math.round(wr.width), header: hdr ? { text: hdr.textContent.trim().slice(0, 40), h: Math.round(hdr.getBoundingClientRect().height), bg: getComputedStyle(hdr).backgroundColor, font: getComputedStyle(hdr).fontSize } : null, buttons: [...wrap.querySelectorAll('button')].map((b) => `${b.getAttribute('aria-label') || ''}|${b.textContent.trim()}`), preFont: getComputedStyle(pre).fontFamily.split(',')[0] + ' ' + getComputedStyle(pre).fontSize, preBg: getComputedStyle(pre).backgroundColor }; });
  const pre = p.locator('pre').last(); await pre.scrollIntoViewIfNeeded(); await pre.hover();
  await p.screenshot({ path: `${out}/${k}-code.png` });
  const cbtn = p.locator('pre').last().locator('xpath=ancestor::*[.//button][1]').getByRole('button', { name: /copy/i }).first();
  if (await cbtn.count()) { await cbtn.click(); await p.waitForTimeout(150); r.codeCopyAfter = `${await cbtn.getAttribute('aria-label')}|${(await cbtn.textContent()).trim()}`; }
  await p.unrouteAll();
  // Scroll-to-bottom: make the thread long, scroll up
  for (let i = 0; i < 3; i++) { await tb.fill('What was our denial rate by payer last quarter?'); await tb.press('Enter'); await send.waitFor({ timeout: 60000 }); }
  await p.mouse.move(vp.width / 2, vp.height / 3); await p.mouse.wheel(0, -4000); await p.waitForTimeout(600);
  const stb = p.getByRole('button', { name: /scroll to|jump to|latest|bottom/i }).first();
  r.scrollBtn = (await stb.count()) ? { label: await stb.getAttribute('aria-label'), ...(await box(p, stb)), centerOffset: await stb.evaluate((e) => { const r = e.getBoundingClientRect(); const c = document.querySelector('textarea').getBoundingClientRect(); return Math.round(r.x + r.width / 2 - (c.x + c.width / 2)); }), gapAboveComposer: await stb.evaluate((e) => Math.round(document.querySelector('textarea').closest('.cc-composer, form').getBoundingClientRect().y - e.getBoundingClientRect().bottom)) } : null;
  await p.screenshot({ path: `${out}/${k}-scroll-btn.png` });
  // Sidebar rows
  if (m) { await p.getByRole('button', { name: 'Open sidebar' }).click(); await p.waitForTimeout(400); }
  const rows = p.getByTestId('sidebar').locator('li');
  const n = await rows.count();
  if (n >= 2) {
    const rowInfo = async (i) => rows.nth(i).evaluate((li) => { const a = li.querySelector('a, button'); const r = a.getBoundingClientRect(); const s = getComputedStyle(a); const lis = getComputedStyle(li); const opt = li.querySelector('[aria-haspopup=menu]'); let o = 1, el = opt; while (el && el !== li.parentElement) { o *= +getComputedStyle(el).opacity; el = el.parentElement; } return { h: Math.round(r.height), radius: s.borderRadius === '0px' ? lis.borderRadius : s.borderRadius, bg: s.backgroundColor !== 'rgba(0, 0, 0, 0)' ? s.backgroundColor : lis.backgroundColor, color: s.color, font: `${s.fontSize}/${s.fontWeight}`, current: a.getAttribute('aria-current') || li.getAttribute('aria-current'), optionsOpacity: opt ? +o.toFixed(2) : null, optionsVisible: opt ? getComputedStyle(opt).visibility : null }; });
    await p.mouse.move(vp.width - 5, vp.height - 5);
    r.sidebarActive = await rowInfo(0); r.sidebarIdle = await rowInfo(1);
    await rows.nth(1).hover(); await p.waitForTimeout(200); r.sidebarHover = await rowInfo(1);
    r.sidebarBg = await p.getByTestId('sidebar').evaluate((e) => getComputedStyle(e).backgroundColor);
    await p.screenshot({ path: `${out}/${k}-sidebar-hover.png`, clip: { x: 0, y: 0, width: Math.min(320, vp.width), height: 300 } });
  }
  await ctx.close();
}
fs.writeFileSync(`${out}/fidelity2.json`, JSON.stringify(R, null, 1)); console.log(JSON.stringify(R, null, 1));
await b.close();
