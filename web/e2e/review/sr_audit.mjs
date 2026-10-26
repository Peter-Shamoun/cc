// QA screen-reader announcement audit: records every live-region change (aria-live, role=log/status/alert)
// with timestamps and aria-busy, during a paced answer on a short thread and on a long (virtualized) thread.
// Usage (paced API on 8140 running): node e2e/review/sr_audit.mjs <outDir>
import { chromium } from 'playwright';
import fs from 'node:fs';
import { distRoutes, LIVE } from '../static.mjs';
const out = process.argv[2]; fs.mkdirSync(out, { recursive: true });
const b = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined });
const ctx = await b.newContext({ viewport: { width: 1440, height: 900 } }); await distRoutes(ctx);
const p = await ctx.newPage(); await p.goto(LIVE);
await p.evaluate(() => {
  window.__ann = []; const t0 = performance.now();
  const regionOf = (n) => { let e = n.nodeType === 1 ? n : n.parentElement; while (e) { const l = e.getAttribute?.('aria-live'); const r = e.getAttribute?.('role'); if ((l && l !== 'off') || ['log', 'status', 'alert'].includes(r)) return e; e = e.parentElement; } return null; };
  const name = (e) => `${e.tagName.toLowerCase()}[${e.getAttribute('role') || ''}|live=${e.getAttribute('aria-live') || (e.getAttribute('role') === 'alert' ? 'assertive' : 'polite')}|${e.getAttribute('data-testid') || e.getAttribute('aria-label') || e.className.toString().slice(0, 30)}]`;
  new MutationObserver((ms) => { for (const m of ms) { const r = regionOf(m.target); if (!r) continue; const busy = r.closest('[aria-busy="true"]') ? 'busy' : '';
    const added = m.type === 'characterData' ? m.target.textContent : [...m.addedNodes].map((n) => n.textContent).join(' ');
    if (added && added.trim()) window.__ann.push({ t: Math.round(performance.now() - t0), region: name(r), busy, text: added.trim().slice(0, 90) }); } })
    .observe(document.body, { subtree: true, childList: true, characterData: true });
});
const ask = async (q) => { const box = p.getByRole('textbox', { name: 'Message' }); await box.fill(q); await box.press('Enter'); await p.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 60000 }); await p.waitForTimeout(300); };
const summarize = (ann) => { const by = {}; for (const a of ann) { by[a.region] ??= { n: 0, busyN: 0 }; by[a.region].n++; if (a.busy) by[a.region].busyN++; } return by; };
const res = {};
await p.evaluate(() => { window.__ann = window.__ann.slice(0, 0); });
await ask('Which CARC codes drive denials?');
let ann = await p.evaluate(() => window.__ann.splice(0));
res.short = { regions: summarize(ann), events: ann.slice(0, 80), regionsPresent: await p.evaluate(() => [...document.querySelectorAll('[aria-live],[role=log],[role=status],[role=alert]')].map((e) => `${e.tagName}|${e.getAttribute('role')}|${e.getAttribute('aria-live')}|${e.getAttribute('data-testid') || e.getAttribute('aria-label') || ''}`)) };
for (let i = 0; i < 14; i++) await ask('Total paid by payer');
await p.evaluate(() => window.__ann.splice(0));
await ask('Which CARC codes drive denials?');
ann = await p.evaluate(() => window.__ann.splice(0));
res.long = { regions: summarize(ann), events: ann.slice(0, 80), regionsPresent: await p.evaluate(() => [...document.querySelectorAll('[aria-live],[role=log],[role=status],[role=alert]')].map((e) => `${e.tagName}|${e.getAttribute('role')}|${e.getAttribute('aria-live')}|${e.getAttribute('data-testid') || e.getAttribute('aria-label') || ''}`)) };
fs.writeFileSync(`${out}/sr_audit.json`, JSON.stringify(res, null, 1));
await b.close();
