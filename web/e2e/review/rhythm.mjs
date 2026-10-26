// QA heuristic: typography rhythm + motion inventory on a live answer. Usage: node e2e/review/rhythm.mjs <outDir>
import { chromium } from 'playwright';
import fs from 'node:fs';
import { distRoutes, LIVE, MOCK } from '../static.mjs';
const URL_ = process.argv[3] === 'mock' ? MOCK : LIVE;
const Q = process.argv[3] === 'mock' ? 'What was our denial rate by payer last quarter?' : 'Which CARC codes drive denials?';
const out = process.argv[2]; fs.mkdirSync(out, { recursive: true });
const b = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined });
const res = {};
for (const [name, vp] of [['desktop', { width: 1440, height: 900 }], ['mobile', { width: 390, height: 844 }]]) {
  const ctx = await b.newContext({ viewport: vp, deviceScaleFactor: 2 });
  await distRoutes(ctx);
  const p = await ctx.newPage();
  await p.goto(URL_);
  const box = p.getByRole('textbox', { name: 'Message' });
  // Motion inventory while streaming: sample animations a few times.
  const anims = new Map();
  const sample = async () => { for (const a of await p.evaluate(() => document.getAnimations().map((a) => {
    const t = a.effect?.getTiming?.() ?? {}; const el = a.effect?.target;
    return { name: a.animationName || a.transitionProperty || a.constructor.name, dur: t.duration, ease: t.easing, it: t.iterations, el: el ? (el.getAttribute('data-testid') || el.className?.toString().split(' ').slice(0, 3).join('.') || el.tagName) : '' };
  }))) anims.set(`${a.name}|${a.dur}|${a.ease}|${a.it}|${a.el}`, a); };
  await box.fill(Q); await box.press('Enter');
  for (let i = 0; i < 12; i++) { await sample(); await p.waitForTimeout(150); }
  await p.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 30000 });
  await p.waitForTimeout(500);
  const typo = await p.evaluate(() => {
    const a = [...document.querySelectorAll('[data-testid="msg-assistant"]')].pop();
    const cs = (el) => el && getComputedStyle(el);
    const prose = a.querySelector('.prose-chat:not(.streaming-caret)') || a; const pick = (sel) => { const el = prose.querySelector(sel); if (!el) return null; const s = cs(el); return { fs: s.fontSize, lh: s.lineHeight, mt: s.marginTop, mb: s.marginBottom, fw: s.fontWeight, ff: s.fontFamily.split(',')[0] }; };
    const ps = [...prose.querySelectorAll('p,ul,ol,h2,h3,table')]; let gaps = [];
    for (let i = 1; i < ps.length; i++) { const g = ps[i].getBoundingClientRect().top - ps[i - 1].getBoundingClientRect().bottom; if (g > 0 && g < 80) gaps.push(Math.round(g)); }
    const turns = [...document.querySelectorAll('[data-testid="msg-user"],[data-testid="msg-assistant"]')];
    const tg = []; for (let i = 1; i < turns.length; i++) tg.push(Math.round(turns[i].getBoundingClientRect().top - turns[i - 1].getBoundingClientRect().bottom));
    const u = document.querySelector('[data-testid="msg-user"] .whitespace-pre-wrap')?.parentElement; const us = cs(u);
    const td = a.querySelector('td'), th = a.querySelector('th');
    return { p: pick('p'), li: pick('li'), ul: pick('ul'), h1: pick('h1'), h2: pick('h2'), h3: pick('h3'), code: pick('code'), pre: pick('pre'),
      paraGapsPx: gaps, turnGapsPx: tg, userBubble: us && { pad: us.padding, fs: us.fontSize, lh: us.lineHeight },
      td: td && { pad: cs(td).padding, fs: cs(td).fontSize, lh: cs(td).lineHeight, h: Math.round(td.getBoundingClientRect().height) }, th: th && { fw: cs(th).fontWeight, fs: cs(th).fontSize },
      tabular: td && cs(td).fontVariantNumeric, proseClass: prose.className.slice(0, 80), maxW: Math.round(prose.getBoundingClientRect().width) };
  });
  const trans = await p.evaluate(() => {
    const out = {}; for (const el of document.querySelectorAll('button,a,[role=menuitem],[data-testid],aside,nav')) {
      const s = getComputedStyle(el); if (s.transitionDuration && s.transitionDuration !== '0s') { const k = `${s.transitionProperty.slice(0, 40)} ${s.transitionDuration} ${s.transitionTimingFunction}`; out[k] = (out[k] || 0) + 1; } }
    return out; });
  // sidebar toggle + popover motion
  await p.screenshot({ path: `${out}/rhythm_${process.argv[3]||'live'}_${name}.png` });
  res[name] = { typo, streamingAnimations: [...anims.values()], transitions: trans };
  await ctx.close();
}
fs.writeFileSync(`${out}/rhythm_${process.argv[3]||'live'}.json`, JSON.stringify(res, null, 1));
await b.close();
