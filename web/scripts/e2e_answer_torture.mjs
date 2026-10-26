// Torture corpus (PR #106 + streaming docs) through the streaming renderer, light + dark, several stream positions.
// Needs the dev server: npx vite --port 5211 --strictPort. Usage: node scripts/e2e_answer_torture.mjs [outDir]
import { chromium } from 'playwright';
import fs from 'node:fs';
const OUT = process.argv[2] ?? '/workspace/cc_fe/shots/answer/e5/torture';
fs.mkdirSync(OUT, { recursive: true });
const KEY = ['unclosed-fence', 'half-table', 'nested-lists', 'rtl-hebrew', 'rtl-arabic', 'emoji', 'long-url', 'display-math', 'unclosed-math', 'mixed-partial',
  'answer-with-table-and-code', 'deep-nested-lists', 'wide-display-math', 'long-urls-in-table', 'rtl-mixed', 'emoji-sequences', 'huge-table', 'fence-with-backticks', 'blockquote-list-code', 'table-wide-50'];
const b = await chromium.launch({ executablePath: '/usr/bin/google-chrome' });
const report = [];
for (const [theme, palette, vw] of [['light', 'neutral', 900], ['dark', 'neutral', 900], ['light', 'paper', 900], ['dark', 'neutral', 390]]) {
  const ctx = await b.newContext({ viewport: { width: vw, height: 1000 }, colorScheme: theme });
  await ctx.addInitScript(([t, p]) => { localStorage.setItem('cc.pref.theme', t); localStorage.setItem('cc.pref.palette', p); }, [theme, palette]);
  const pg = await ctx.newPage();
  const errs = [];
  pg.on('pageerror', (e) => errs.push(String(e)));
  pg.on('console', (m) => { if (m.type() === 'error') errs.push(m.text().slice(0, 200)); });
  for (const frac of [0.33, 0.66, 1]) {
    await pg.goto(`http://127.0.0.1:5211/dev-torture.html?frac=${frac}`);
    await pg.waitForSelector('body[data-ready]', { timeout: 60000 });
    await pg.waitForTimeout(1200);
    const res = await pg.evaluate(() => [...document.querySelectorAll('[data-case]')].map((s) => {
      const md = s.querySelector('[data-testid="markdown"]');
      const clone = (md ?? s).cloneNode(true);
      clone.querySelectorAll('pre, code, .katex, [data-testid="code-block"]').forEach((n) => n.remove());
      const t = clone.textContent ?? '';
      const g = [];
      if (s.scrollWidth > s.clientWidth + 1) g.push(`overflow ${s.scrollWidth}>${s.clientWidth}`);
      if (/```|~~~/.test(t)) g.push('raw fence');
      if (/\|\s*:?-{3,}/.test(t)) g.push('raw table rule');
      if (/\$\$/.test(t)) g.push('raw $$');
      if (md && md.querySelector('.katex-error')) g.push('katex error');
      return { id: s.getAttribute('data-case'), g };
    }));
    const docOverflow = await pg.evaluate(() => document.documentElement.scrollWidth > document.documentElement.clientWidth);
    for (const r of res) if (r.g.length) report.push(`${theme}/${palette}/${vw} ${frac} ${r.id}: ${r.g.join(', ')}`);
    if (docOverflow) report.push(`${theme}/${palette}/${vw} ${frac} PAGE horizontal overflow`);
    if (frac !== 0.33 || vw === 390) {
      for (const id of KEY) {
        const el = pg.locator(`[data-case="${id}"]`);
        if (await el.count()) await el.screenshot({ path: `${OUT}/${theme}_${palette}_${vw}_${Math.round(frac * 100)}_${id}.png` }).catch(() => {});
      }
    }
  }
  if (errs.length) report.push(`${theme}/${palette}/${vw} console: ${[...new Set(errs)].slice(0, 5).join(' | ')}`);
  await ctx.close();
}
await b.close();
fs.writeFileSync(`${OUT}/report.txt`, report.join('\n') + '\n');
console.log(report.length ? report.join('\n') : 'no glitches');
