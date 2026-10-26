// Anti-slop + fidelity audit (PETER DIRECTIVE 10:06 PM). Every key screen × light/dark × 1440/390.
// Measures computed styles against the BRIEF banned list and the Claude/ChatGPT observed conventions.
// Usage (demo API on 8140): node e2e/review/slop_audit.mjs <outDir>
import { chromium } from 'playwright';
import fs from 'node:fs';
import path from 'node:path';
import { distRoutes, LIVE } from '../static.mjs';
const out = process.argv[2]; fs.mkdirSync(out, { recursive: true });
const EX = path.resolve(import.meta.dirname, '../../../api/contract/examples');
const ex = (n) => fs.readFileSync(path.join(EX, n + '.jsonl'), 'utf8').trim().split('\n').filter((l) => l.trim() && !l.startsWith('//')).map((l) => JSON.parse(l));
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome' });
const res = {};

const measure = () => {
  const vis = (e) => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && +s.opacity > 0.05; };
  const parse = (c) => { const m = c.match(/rgba?\(([^)]+)\)/); if (!m) return null; const [r, g, bl, a = 1] = m[1].split(/[ ,/]+/).filter(Boolean).map(Number); return { r, g, b: bl, a }; };
  const sat = ({ r, g, b }) => { const mx = Math.max(r, g, b), mn = Math.min(r, g, b); return mx === 0 ? 0 : (mx - mn) / mx; };
  const hue = ({ r, g, b }) => { const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn; if (!d) return 0; let h = mx === r ? ((g - b) / d) % 6 : mx === g ? (b - r) / d + 2 : (r - g) / d + 4; return Math.round((h * 60 + 360) % 360); };
  const els = [...document.querySelectorAll('body *')].filter(vis);
  const accents = {}; const gradients = []; const blurs = []; const shadows = []; const glows = []; const pills = []; const cards = []; const anims = new Set(); const emoji = [];
  for (const e of els) {
    const s = getComputedStyle(e);
    const tag = `${e.tagName.toLowerCase()}${e.getAttribute('data-testid') ? '#' + e.getAttribute('data-testid') : ''}${e.getAttribute('aria-label') ? '[' + e.getAttribute('aria-label').slice(0, 24) + ']' : ''}`;
    for (const prop of ['color', 'backgroundColor', 'borderTopColor', 'fill', 'stroke']) {
      const c = parse(s[prop] || ''); if (!c || c.a < 0.3) continue;
      if (prop === 'borderTopColor' && parseFloat(s.borderTopWidth) === 0) continue;
      if ((prop === 'fill' || prop === 'stroke') && !(e instanceof SVGElement)) continue;
      if (sat(c) > 0.35 && Math.max(c.r, c.g, c.b) > 60) { const h = Math.round(hue(c) / 30) * 30 % 360; (accents[h] ??= { n: 0, ex: [] }).n++; if (accents[h].ex.length < 3) accents[h].ex.push(`${tag}.${prop}=${s[prop]}`); }
    }
    if (/gradient/.test(s.backgroundImage) && !/mask/.test(s.backgroundImage)) gradients.push(`${tag}: ${s.backgroundImage.slice(0, 80)}`);
    if (/gradient/.test(s.webkitBackgroundClip === 'text' ? s.backgroundImage : '')) gradients.push(`TEXT ${tag}`);
    if ((s.backdropFilter && s.backdropFilter !== 'none') || /blur/.test(s.filter)) blurs.push(`${tag}: ${s.backdropFilter} ${s.filter}`);
    if (s.boxShadow !== 'none') {
      const blursPx = [...s.boxShadow.matchAll(/(-?\d+(?:\.\d+)?)px (-?\d+(?:\.\d+)?)px (\d+(?:\.\d+)?)px/g)].map((m) => +m[3]);
      const maxBlur = Math.max(0, ...blursPx);
      const coloured = [...s.boxShadow.matchAll(/rgba?\([^)]+\)/g)].map((m) => parse(m[0])).some((c) => c && sat(c) > 0.35 && c.a > 0.15);
      if (coloured && maxBlur > 0) glows.push(`${tag}: ${s.boxShadow.slice(0, 90)}`);
      else if (maxBlur >= 24) shadows.push(`${tag}: ${s.boxShadow.slice(0, 90)}`);
    }
    const r = e.getBoundingClientRect();
    if ((e.tagName === 'BUTTON' || e.getAttribute('role') === 'button' || e.tagName === 'A') && (e.textContent || '').trim().length > 2 && r.width > r.height * 1.6 && parseFloat(s.borderTopLeftRadius) >= r.height / 2 - 1) pills.push(`${tag}:${(e.textContent || '').trim().slice(0, 24)}`);
    if (parseFloat(s.borderTopLeftRadius) >= 8 && parseFloat(s.borderTopWidth) > 0 && r.width > 160 && r.height > 48 && e.closest('[data-testid=msg-assistant]')) cards.push(`${tag} ${Math.round(r.width)}x${Math.round(r.height)}`);
    if (s.animationName && s.animationName !== 'none') anims.add(`${s.animationName} ${s.animationDuration} ${s.animationIterationCount}`);
    if (s.transitionTimingFunction && /cubic-bezier\([^)]*(1\.[1-9]|-0\.[1-9])/.test(s.transitionTimingFunction)) anims.add(`OVERSHOOT ${tag} ${s.transitionTimingFunction}`);
    if (e.childElementCount === 0 && /[\u{1F300}-\u{1FAFF}\u{2728}\u{2B50}]/u.test(e.textContent || '') && !e.closest('[data-testid=msg-assistant] .prose, [data-testid=msg-user]')) emoji.push(`${tag}:${(e.textContent || '').trim().slice(0, 20)}`);
  }
  const icons = [...document.querySelectorAll('svg.lucide')].filter(vis).map((e) => `${Math.round(e.getBoundingClientRect().width)}/${e.getAttribute('stroke-width') || getComputedStyle(e).strokeWidth}`);
  const iconHist = icons.reduce((m, k) => ((m[k] = (m[k] || 0) + 1), m), {});
  const sparkleIcons = [...document.querySelectorAll('svg.lucide-sparkles, svg.lucide-sparkle, svg.lucide-wand-sparkles, svg.lucide-stars')].filter(vis).length;
  const fs_ = (sel) => { const e = document.querySelector(sel); if (!e || !vis(e)) return null; const s = getComputedStyle(e); const r = e.getBoundingClientRect(); return { font: s.fontFamily.split(',')[0].replace(/"/g, ''), size: s.fontSize, lh: s.lineHeight, weight: s.fontWeight, w: Math.round(r.width), color: s.color }; };
  const sidebar = document.querySelector('[data-testid=sidebar]'); const main = document.querySelector('main');
  const headings = [...document.querySelectorAll('h1,h2')].filter(vis).map((h) => `${h.tagName}:${getComputedStyle(h).fontSize}/${getComputedStyle(h).fontWeight}:${h.textContent.trim().slice(0, 30)}`);
  return {
    accents, accentHues: Object.keys(accents).length, gradients: gradients.slice(0, 8), blurs: blurs.slice(0, 5), shadows: shadows.slice(0, 6), glows: glows.slice(0, 5), pills: pills.slice(0, 12), pillCount: pills.length,
    cards: cards.slice(0, 10), cardCount: cards.length, anims: [...anims].slice(0, 12), emoji, sparkleIcons, iconHist,
    body: fs_('body'), prose: fs_('[data-testid=msg-assistant] p'), userBubble: fs_('[data-testid=msg-user]'), composer: fs_('textarea'), headings,
    column: (() => { const p = document.querySelector('[data-testid=msg-assistant]'); return p ? Math.round(p.getBoundingClientRect().width) : null; })(),
    sidebarBg: sidebar ? getComputedStyle(sidebar).backgroundColor : null, mainBg: main ? getComputedStyle(main).backgroundColor : getComputedStyle(document.body).backgroundColor,
    sidebarW: sidebar && vis(sidebar) ? Math.round(sidebar.getBoundingClientRect().width) : null,
  };
};

const screens = [
  ['empty', async () => {}],
  ['answer', async (p, inj) => { await inj(ex('answer')); await send(p, 'denial rate by payer'); }],
  ['steps', async (p, inj) => { await inj(ex('tool_steps')); await send(p, 'tool steps'); const t = p.getByRole('button', { name: /Thought for|steps/i }).first(); if (await t.count()) await t.click().catch(() => {}); }],
  ['chart', async (p, inj) => { await inj(ex('chart')); await send(p, 'chart'); }],
  ['clarify', async (p, inj) => { await inj(ex('clarify')); await send(p, 'trend'); }],
  ['abstain', async (p, inj) => { await inj(ex('abstain')); await send(p, 'names'); }],
  ['error', async (p, inj) => { await inj(ex('error')); await send(p, 'error'); }],
  ['report', async (p, inj) => { await inj(ex('report')); await send(p, 'report'); }],
  ['edit', async (p, inj) => { await inj(ex('answer')); await send(p, 'denial rate by payer'); await p.getByTestId('msg-user').last().hover(); await p.getByRole('button', { name: /^edit/i }).last().click(); }],
  ['settings', async (p) => { await p.keyboard.press('Control+,'); }],
  ['search', async (p) => { await p.keyboard.press('Control+k'); }],
  ['shortcuts', async (p) => { await p.keyboard.press('Control+/'); }],
  ['share', async (p) => { await send(p, 'Total paid by payer'); await p.getByTestId('share-button').click(); }],
  ['drawer', async (p) => { const o = p.getByRole('button', { name: 'Open sidebar' }); if (await o.count() && await o.isVisible()) await o.click(); }],
];
async function send(p, q) { const box = p.getByRole('textbox', { name: 'Message' }); await box.fill(q); await box.press('Enter'); await p.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 }); await p.waitForTimeout(500); }

for (const [vp, tag] of [[{ width: 1440, height: 900 }, 'desktop'], [{ width: 390, height: 844 }, 'mobile']]) for (const theme of ['light', 'dark']) {
  const m = vp.width < 500;
  for (const [name, act] of screens) {
    if (name === 'drawer' && !m) continue;
    const ctx = await b.newContext({ viewport: vp, colorScheme: theme, isMobile: m, hasTouch: m, deviceScaleFactor: m ? 2 : 1, reducedMotion: 'no-preference' });
    await distRoutes(ctx); await ctx.addInitScript((t) => { try { localStorage.setItem('cc.theme', t); } catch {} }, theme);
    const p = await ctx.newPage();
    const inj = (evs) => p.route('**/api/chat', (r) => r.request().method() === 'POST' ? r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: evs.map((e, i) => `id: ${i}\ndata: ${JSON.stringify(e)}\n\n`).join('') }) : r.continue());
    const key = `${tag}-${theme}-${name}`;
    try {
      await p.goto(LIVE); await p.getByRole('textbox', { name: 'Message' }).waitFor();
      await act(p, inj); await p.waitForTimeout(700);
      await p.screenshot({ path: `${out}/${key}.png` });
      res[key] = await p.evaluate(measure);
    } catch (e) { res[key] = { error: e.message.split('\n')[0].slice(0, 160) }; }
    await ctx.close();
  }
}
fs.writeFileSync(`${out}/slop.json`, JSON.stringify(res, null, 1));
// Roll-up
const roll = { gradients: new Set(), blurs: new Set(), shadows: new Set(), glows: new Set(), pills: new Set(), cards: new Set(), anims: new Set(), emoji: new Set(), accentHues: {}, sparkle: [], icon: {}, type: {} };
for (const [k, r] of Object.entries(res)) { if (r.error) { console.log('ERR', k, r.error); continue; }
  for (const f of ['gradients', 'blurs', 'shadows', 'glows', 'pills', 'cards', 'anims', 'emoji']) for (const x of r[f]) roll[f].add(x.replace(/\d+x\d+$/, ''));
  for (const [h, v] of Object.entries(r.accents)) { roll.accentHues[h] ??= new Set(); v.ex.forEach((e) => roll.accentHues[h].add(`${k.split('-').slice(0, 2).join('-')}: ${e}`)); }
  if (r.sparkleIcons) roll.sparkle.push(k);
  for (const [i, n] of Object.entries(r.iconHist)) roll.icon[i] = (roll.icon[i] || 0) + n;
  roll.type[k] = { body: r.body && `${r.body.font} ${r.body.size}/${r.body.lh}`, prose: r.prose && `${r.prose.size}/${r.prose.lh} w${r.prose.weight}`, column: r.column, composerW: r.composer?.w, headings: r.headings.slice(0, 3), sidebar: `${r.sidebarW}px ${r.sidebarBg}`, main: r.mainBg };
}
const ser = Object.fromEntries(Object.entries(roll).map(([k, v]) => [k, v instanceof Set ? [...v] : k === 'accentHues' ? Object.fromEntries(Object.entries(v).map(([h, s]) => [h, [...s].slice(0, 6)])) : v]));
fs.writeFileSync(`${out}/slop_rollup.json`, JSON.stringify(ser, null, 1)); console.log(JSON.stringify(ser, null, 1));
await b.close();
