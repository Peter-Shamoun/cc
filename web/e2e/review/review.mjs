// QA SOTA parity review: screenshot every screen/state and collect measurable rubric signals.
// Usage: node e2e/review/review.mjs <outDir> [live=http://127.0.0.1:5240/] [mock=http://127.0.0.1:5241/]
import { chromium } from 'playwright';
import AxeBuilder from '@axe-core/playwright';
import fs from 'node:fs';
import path from 'node:path';

const [out, LIVE = 'http://127.0.0.1:5240/', MOCK = 'http://127.0.0.1:5241/'] = process.argv.slice(2);
fs.mkdirSync(out, { recursive: true });
const browser = await chromium.launch({ executablePath: fs.existsSync('/usr/bin/google-chrome') ? '/usr/bin/google-chrome' : undefined });
const report = { shots: [], axe: {}, metrics: {}, console: [], errors: [] };
const contractDir = path.resolve(path.dirname(new URL(import.meta.url).pathname), '../../../api/contract/examples');

async function newPage(url, { theme = 'light', palette = 'neutral', w = 1440, h = 900, mobile = false, reduced = false } = {}) {
  const ctx = await browser.newContext({ viewport: { width: w, height: h }, deviceScaleFactor: mobile ? 2 : 1, isMobile: mobile, hasTouch: mobile, reducedMotion: reduced ? 'reduce' : 'no-preference', acceptDownloads: true });
  await ctx.addInitScript(([t, p]) => { localStorage.setItem('cc.pref.theme', t); localStorage.setItem('cc.pref.palette', p); }, [theme, palette]);
  const pg = await ctx.newPage();
  pg.on('console', (m) => { if (m.type() === 'error' || m.type() === 'warning') report.console.push(`${m.type()}: ${m.text().slice(0, 200)}`); });
  pg.on('pageerror', (e) => report.errors.push(String(e).slice(0, 300)));
  await pg.goto(url);
  await pg.getByRole('textbox', { name: 'Message' }).waitFor();
  return pg;
}
const tb = (pg) => pg.getByRole('textbox', { name: 'Message' });
async function idle(pg) {
  await pg.getByRole('button', { name: /stop/i }).waitFor({ state: 'detached', timeout: 45000 }).catch(() => {});
  await pg.getByRole('button', { name: 'Send message' }).waitFor({ timeout: 45000 }).catch(() => {});
  await pg.waitForTimeout(500);
}
async function ask(pg, q, wait = true) { await tb(pg).fill(q); await tb(pg).press('Enter'); if (wait) await idle(pg); }
async function shot(pg, name, opts = {}) {
  const f = path.join(out, `${String(report.shots.length + 1).padStart(2, '0')}_${name}.png`);
  await pg.screenshot({ path: f, ...opts }).catch((e) => report.errors.push(`shot ${name}: ${e.message.slice(0, 120)}`));
  report.shots.push(path.basename(f));
}
async function axe(pg, name) {
  const r = await new AxeBuilder({ page: pg }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'best-practice']).analyze();
  report.axe[name] = r.violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, target: v.nodes.slice(0, 3).map((x) => x.target.join(' ')) }));
}
async function safe(name, fn) { try { await fn(); } catch (e) { report.errors.push(`${name}: ${e.message.split('\n')[0].slice(0, 200)}`); } }
async function measure(pg) {
  return pg.evaluate(() => {
    const cs = (el) => (el ? getComputedStyle(el) : null);
    const q = (s) => document.querySelector(s);
    const md = q('[data-testid="msg-assistant"] [data-testid="markdown"] p') ?? q('[data-testid="markdown"]');
    const user = q('[data-testid="msg-user"]');
    const userBubble = user ? [...user.querySelectorAll('*')].find((e) => cs(e).backgroundColor !== 'rgba(0, 0, 0, 0)') : null;
    const comp = q('[data-testid="composer"]');
    const compBox = comp ? [comp, ...comp.querySelectorAll('*')].find((e) => parseFloat(cs(e).borderTopLeftRadius) > 10) : null;
    const col = md?.closest('.mx-auto') ?? null;
    return {
      proseFontPx: md ? parseFloat(cs(md).fontSize) : null,
      proseLineHeight: md ? (parseFloat(cs(md).lineHeight) / parseFloat(cs(md).fontSize)).toFixed(2) : null,
      proseFont: md ? cs(md).fontFamily.split(',')[0] : null,
      columnWidthPx: col ? Math.round(col.getBoundingClientRect().width) : null,
      userBubbleRadius: userBubble ? cs(userBubble).borderTopLeftRadius : null,
      userBubbleBg: userBubble ? cs(userBubble).backgroundColor : null,
      userAlign: user ? (user.getBoundingClientRect().right > window.innerWidth / 2 + 200 ? 'right' : 'left') : null,
      composerRadius: compBox ? cs(compBox).borderTopLeftRadius : null,
      composerHeight: comp ? Math.round(comp.getBoundingClientRect().height) : null,
      liveRegions: document.querySelectorAll('[aria-live],[role=log],[role=status]').length,
      landmarks: ['main', 'nav', 'aside', 'header'].map((t) => `${t}:${document.querySelectorAll(t).length}`).join(' '),
      h1: document.querySelectorAll('h1').length,
      domNodes: document.querySelectorAll('*').length,
    };
  });
}

// ---------- 1. Empty states: desktop/mobile x light/dark x palettes ----------
for (const [theme, palette] of [['light', 'neutral'], ['dark', 'neutral'], ['light', 'paper'], ['dark', 'paper']]) {
  await safe(`empty ${theme} ${palette}`, async () => {
    const pg = await newPage(MOCK, { theme, palette });
    await shot(pg, `empty_desktop_${palette}_${theme}`);
    if (palette === 'neutral') await axe(pg, `empty_${theme}`);
    const m = await newPage(MOCK, { theme, palette, w: 390, h: 844, mobile: true });
    await shot(m, `empty_mobile_${palette}_${theme}`);
    await pg.context().close(); await m.context().close();
  });
}

// ---------- 2. Live round trip ----------
await safe('live', async () => {
  const pg = await newPage(LIVE);
  await tb(pg).fill('What is the denial rate by payer?');
  await shot(pg, 'live_composer_filled');
  await tb(pg).press('Enter');
  await pg.waitForTimeout(120);
  await shot(pg, 'live_streaming_early');
  await idle(pg);
  await shot(pg, 'live_table_answer');
  report.metrics.live_answer = await measure(pg);
  await axe(pg, 'live_answer_light');
  await safe('sql', async () => { await pg.getByTestId('sql-disclosure').first().getByRole('button').first().click(); await pg.waitForTimeout(300); await shot(pg, 'live_sql_expanded'); });
  await safe('hover actions', async () => { await pg.getByTestId('msg-assistant').last().hover(); await shot(pg, 'live_hover_actions'); });
  await safe('table hover', async () => { await pg.getByTestId('data-table').first().hover(); await shot(pg, 'live_table_hover'); });
  await safe('artifact', async () => { const c = pg.getByTestId('artifact-card').first(); if (await c.count()) { await c.click(); await pg.waitForTimeout(500); await shot(pg, 'live_artifact_panel'); await pg.keyboard.press('Escape'); await pg.waitForTimeout(300); await shot(pg, 'live_artifact_after_esc'); } });
  await ask(pg, 'Show the monthly trend of paid amount');
  await shot(pg, 'live_chart_answer');
  await ask(pg, 'List the patient names and member IDs for denied claims');
  await shot(pg, 'live_refusal');
  await shot(pg, 'live_full_thread', { fullPage: true });
  await pg.context().close();
});

// ---------- 3. Dark live answer + paper ----------
await safe('dark live', async () => {
  const pg = await newPage(LIVE, { theme: 'dark' });
  await ask(pg, 'What is the denial rate by payer?');
  await shot(pg, 'live_table_answer_dark');
  await axe(pg, 'live_answer_dark');
  await pg.context().close();
  const pp = await newPage(LIVE, { palette: 'paper' });
  await ask(pp, 'Total paid by payer');
  await shot(pp, 'live_paper_answer');
  report.metrics.paper_answer = await measure(pp);
  await pp.context().close();
});

// ---------- 4. Mock: streaming states, tool steps, stop, error, edit, feedback, search ----------
await safe('mock flows', async () => {
  const pg = await newPage(MOCK);
  await ask(pg, 'Which CARC codes drive Cascade Medicaid denials?', false);
  await pg.waitForTimeout(250); await shot(pg, 'mock_tool_running');
  await pg.waitForTimeout(700); await shot(pg, 'mock_streaming_mid');
  await idle(pg); await shot(pg, 'mock_carc_done');
  report.metrics.mock_answer = await measure(pg);
  await ask(pg, 'Plot the monthly denial rate trend for our top 3 payers', false);
  await pg.waitForTimeout(400); await shot(pg, 'mock_followup_pinned_streaming');
  await pg.getByRole('button', { name: /stop/i }).click().catch(() => {});
  await idle(pg); await shot(pg, 'mock_stopped');
  await ask(pg, 'Run the query against the raw 837 tables'); await shot(pg, 'mock_error_state');
  await safe('edit', async () => {
    await pg.getByTestId('msg-user').first().hover();
    await pg.getByRole('button', { name: 'Edit message' }).first().click();
    await shot(pg, 'mock_edit_inline');
    await pg.getByRole('textbox', { name: 'Edit message' }).fill('Which CARC codes drive Aetna denials?');
    await pg.getByRole('button', { name: 'Send', exact: true }).click(); await idle(pg);
    await pg.evaluate(() => document.querySelector('main .overflow-y-auto')?.scrollTo(0, 0));
    await shot(pg, 'mock_branch_2of2');
  });
  await safe('feedback', async () => { await pg.getByRole('button', { name: 'Bad response' }).last().click(); await pg.waitForTimeout(300); await shot(pg, 'mock_feedback_panel'); await pg.keyboard.press('Escape'); });
  await safe('search', async () => { await pg.keyboard.press('Control+k'); await pg.waitForTimeout(300); await pg.keyboard.type('carc'); await pg.waitForTimeout(300); await shot(pg, 'mock_search'); await pg.keyboard.press('Escape'); });
  await safe('focus', async () => { await pg.locator('body').click({ position: { x: 700, y: 60 } }); for (let i = 0; i < 6; i++) await pg.keyboard.press('Tab'); await shot(pg, 'mock_keyboard_focus'); report.metrics.focusVisible = await pg.evaluate(() => { const a = document.activeElement; if (!a) return null; const s = getComputedStyle(a); return `${a.tagName} ${a.getAttribute('aria-label') ?? ''} outline=${s.outlineStyle} ${s.outlineWidth} shadow=${s.boxShadow !== 'none'}`; }); });
  await pg.context().close();
});

// ---------- 5. Mobile flows ----------
await safe('mobile', async () => {
  for (const theme of ['light', 'dark']) {
    const pg = await newPage(MOCK, { theme, w: 390, h: 844, mobile: true });
    await ask(pg, 'What was our denial rate by payer last quarter?');
    await shot(pg, `mobile_table_answer_${theme}`);
    report.metrics[`mobile_overflow_${theme}`] = await pg.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    if (theme === 'light') {
      await ask(pg, 'Plot the monthly denial rate trend for our top 3 payers'); await shot(pg, 'mobile_chart');
      await pg.getByRole('button', { name: 'Open sidebar' }).click().catch(() => {}); await pg.waitForTimeout(400); await shot(pg, 'mobile_drawer');
      await tb(pg).focus().catch(() => {});
    }
    await pg.context().close();
  }
});

// ---------- 6. Contract outcomes (if published) ----------
for (const o of ['answer', 'tool_steps', 'abstain', 'clarify', 'escalation']) {
  const f = ['.jsonl', '.ndjson'].map((e) => path.join(contractDir, o + e)).find((x) => fs.existsSync(x));
  if (!f) { report.metrics[`contract_${o}`] = 'missing'; continue; }
  await safe(`contract ${o}`, async () => {
    const evs = fs.readFileSync(f, 'utf8').trim().split('\n').filter((l) => l.trim() && !l.startsWith('//')).map((l) => JSON.parse(l));
    for (const theme of ['light', 'dark']) {
      const pg = await newPage(LIVE, { theme });
      await pg.route('**/api/chat', (r) => r.request().method() === 'POST' ? r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: evs.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') }) : r.continue());
      await ask(pg, `contract ${o}`);
      await shot(pg, `contract_${o}_${theme}`);
      report.metrics[`contract_${o}`] = (await pg.getByTestId('unsupported').count()) ? 'unsupported-chip' : 'rendered';
      await pg.context().close();
    }
  });
}

// ---------- 7. Big thread + 10k rows ----------
await safe('perf', async () => {
  const pg = await newPage(LIVE);
  const cols = [{ key: 'c', label: 'Claim', type: 'code' }, { key: 'p', label: 'Payer', type: 'string' }, { key: 'b', label: 'Billed', type: 'currency' }, { key: 'r', label: 'Denial rate', type: 'percent' }];
  const rows = Array.from({ length: 10000 }, (_, i) => [`CLM-${100000 + i}`, ['Aetna', 'BCBS', 'Cigna'][i % 3], 100 + (i % 900), (i % 100) / 1000]);
  await pg.route('**/api/chat', (r) => r.request().method() === 'POST' ? r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: [
    { type: 'message.start', messageId: 'm1', conversationId: 'c1' }, { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: 'All 10,000 claims.' }, { type: 'text.end', partId: 't' },
    { type: 'part', part: { type: 'table', id: 'tb', title: 'All claims', columns: cols, rows, rowCount: 10000 } }, { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 1, elapsedMs: 900 } },
  ].map((e) => `data: ${JSON.stringify(e)}\n\n`).join('') }) : r.continue());
  await ask(pg, 'all claims');
  await shot(pg, 'perf_10k_table');
  await safe('10k panel', async () => { const c = pg.getByTestId('artifact-card').first(); if (await c.count()) { await c.click(); await pg.waitForTimeout(600); await shot(pg, 'perf_10k_table_panel'); } });
  await pg.context().close();
});

// ---------- 8. Contract v1 scenarios on the live demo (C2+). PACED = API with realistic model/stream delays ----------
const PACED = process.env.REVIEW_PACED_URL ?? LIVE;
const sse = (evs) => evs.map((e) => `data: ${JSON.stringify(e)}\n\n`).join('');
await safe('v1 repair paced', async () => {
  const pg = await newPage(PACED);
  const t0 = Date.now();
  await ask(pg, 'Which CARC codes drive denials?', false);
  await pg.getByTestId('step-timeline').first().waitFor({ timeout: 15000 }).catch(() => {});
  report.metrics.firstStepMs = Date.now() - t0;
  await pg.waitForTimeout(300); await shot(pg, 'v1_repair_live_steps');
  report.metrics.sidebarDuringStream = await pg.getByTestId('thread-item').count();
  await pg.getByTestId('markdown').first().waitFor({ timeout: 20000 }).catch(() => {});
  report.metrics.firstTextMs = Date.now() - t0;
  await shot(pg, 'v1_repair_text_streaming');
  await idle(pg); await shot(pg, 'v1_repair_done_collapsed');
  await safe('expand steps', async () => { await pg.getByTestId('step-summary').first().click(); await pg.waitForTimeout(350); await shot(pg, 'v1_repair_steps_expanded'); });
  await safe('sql inspector', async () => { await pg.getByTestId('open-sql').first().click(); await pg.waitForTimeout(400); await shot(pg, 'v1_sql_inspector'); await pg.keyboard.press('Escape'); });
  await pg.context().close();
});
await safe('v1 clarify', async () => {
  const pg = await newPage(LIVE);
  await ask(pg, 'How bad are our denials?');
  await shot(pg, 'v1_clarify_card');
  await axe(pg, 'v1_clarify');
  await pg.getByTestId('clarify-chip').first().click(); await idle(pg);
  await shot(pg, 'v1_clarify_resumed');
  await pg.context().close();
});
await safe('v1 abstain', async () => {
  const pg = await newPage(LIVE);
  await ask(pg, 'Forecast next quarter denials');
  await shot(pg, 'v1_abstain_card');
  await axe(pg, 'v1_abstain');
  await pg.context().close();
});
await safe('v1 escalation + cost', async () => {
  for (const theme of ['light', 'dark']) {
    const pg = await newPage(LIVE, { theme });
    await ask(pg, 'What is our net collection rate by payer?');
    await shot(pg, `v1_escalation_${theme}`);
    if (theme === 'light') {
      const meta = pg.getByTestId('meta-line').or(pg.getByTestId('answer-meta')).first();
      await meta.hover(); await meta.click().catch(() => {}); await pg.waitForTimeout(300);
      await shot(pg, 'v1_cost_popover');
    }
    await pg.context().close();
  }
});
await safe('v1 candidates', async () => {
  const pg = await newPage(LIVE);
  await ask(pg, 'Double check the average paid per claim');
  if (!(await pg.getByTestId('candidates').count())) await pg.getByTestId('step-summary').first().click().catch(() => {});
  await pg.waitForTimeout(300); await shot(pg, 'v1_candidates');
  await pg.context().close();
});
await safe('v1 error + slow stop', async () => {
  const pg = await newPage(LIVE);
  await ask(pg, 'Simulate an error please'); await shot(pg, 'v1_error_card');
  await ask(pg, 'Run this slowly: denial rate by payer', false);
  await pg.waitForTimeout(600); await shot(pg, 'v1_slow_running');
  await pg.getByRole('button', { name: /stop/i }).click().catch(() => {}); await idle(pg);
  await shot(pg, 'v1_slow_stopped');
  await pg.context().close();
});
await safe('katex', async () => {
  const pg = await newPage(LIVE);
  const md = 'The **denial rate** is\n\n$$\\text{denial rate} = \\frac{\\text{denied claims}}{\\text{adjudicated claims}} = \\frac{1{,}284}{9{,}410} \\approx 13.6\\%$$\n\nInline: $r = d/n$. Currency stays text: Aetna paid $1,250 and $300 in adjustments.';
  await pg.route('**/api/chat', (r) => r.request().method() === 'POST' ? r.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: sse([
    { type: 'message.start', messageId: 'k1', conversationId: 'ck' }, { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: md }, { type: 'text.end', partId: 't' },
    { type: 'message.end', messageId: 'k1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 300 } }]) }) : r.continue());
  await ask(pg, 'How is denial rate defined?');
  await shot(pg, 'katex_answer');
  report.metrics.katexNodes = await pg.locator('.katex').count();
  await pg.context().close();
});
await safe('shell v2', async () => {
  const pg = await newPage(LIVE);
  await ask(pg, 'Total paid by payer');
  await ask(pg, 'Show the monthly trend of paid amount');
  await pg.keyboard.press('Control+/'); await pg.waitForTimeout(300); await shot(pg, 'shell_shortcuts_dialog'); await pg.keyboard.press('Escape');
  await safe('settings', async () => { await pg.getByRole('button', { name: /settings/i }).first().click().catch(async () => { await pg.getByTestId('account-button').click(); await pg.getByRole('menuitem', { name: /settings/i }).click(); }); await pg.waitForTimeout(300); await shot(pg, 'shell_settings_dialog'); await axe(pg, 'shell_settings'); await pg.keyboard.press('Escape'); });
  await safe('slash', async () => { await tb(pg).fill('/'); await pg.waitForTimeout(300); await shot(pg, 'shell_slash_menu'); await tb(pg).fill(''); });
  await safe('search v2', async () => { await pg.keyboard.press('Control+k'); await pg.keyboard.type('paid'); await pg.waitForTimeout(500); await shot(pg, 'shell_search_fulltext'); report.metrics.searchHits = await pg.getByTestId('search-result').count(); await pg.keyboard.press('Escape'); });
  await safe('thread menu', async () => { const it = pg.getByTestId('thread-item').first(); await it.hover(); await it.getByRole('button').last().click(); await pg.waitForTimeout(250); await shot(pg, 'shell_thread_menu'); await pg.keyboard.press('Escape'); });
  report.metrics.shell_v2 = await measure(pg);
  await pg.context().close();
});

fs.writeFileSync(path.join(out, 'report.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify({ shots: report.shots.length, errors: report.errors, axe: Object.fromEntries(Object.entries(report.axe).map(([k, v]) => [k, v.map((x) => `${x.id}:${x.impact}:${x.n}`)])), metrics: report.metrics, console: [...new Set(report.console)].slice(0, 15) }, null, 1));
await browser.close();
