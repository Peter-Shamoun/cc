import fs from 'node:fs';
import { test, expect } from './fixtures';
import { LIVE, open, ask, lastAssistant, composer, waitIdle } from './helpers';

/**
 * Long-session soak on the real demo API: 50 turns of mixed tables, charts, step timelines and
 * comparisons, with an edit and a regenerate every 10 turns. Samples heap after GC, DOM nodes,
 * listeners and layout counts, then measures frame times while scrolling and typing. Writes
 * test-results/soak.json (or $SOAK_OUT). Budgets are loose leak guards; the trend goes to the scorecard.
 */
const PROMPTS = [
  'What is the denial rate by payer?',
  'Plot the monthly denial rate trend for our top 3 payers',
  'Which CARC codes drive denials?',
  'Total paid by payer',
  'Double check the average paid per claim',
];

test('50-turn mixed session: heap, DOM and frames stay bounded @perf', async ({ page }) => {
  test.setTimeout(900_000);
  await open(page, LIVE);
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Performance.enable');
  const sample = async (turn: number) => {
    await cdp.send('HeapProfiler.collectGarbage');
    const { metrics } = await cdp.send('Performance.getMetrics');
    const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]));
    const dom = await page.evaluate(() => document.querySelectorAll('*').length);
    return { turn, heapMB: +(m.JSHeapUsedSize / 1048576).toFixed(1), domNodes: dom, nodes: m.Nodes, listeners: m.JSEventListeners, layouts: m.LayoutCount, docs: m.Documents };
  };
  const samples = [await sample(0)];
  const turnMs: number[] = [];
  for (let i = 1; i <= 50; i++) {
    const t0 = Date.now();
    await ask(page, PROMPTS[i % PROMPTS.length]);
    turnMs.push(Date.now() - t0);
    if (i % 10 === 0) {
      // Regenerate the last answer, then edit the last question.
      await lastAssistant(page).getByRole('button', { name: /^Regenerate/ }).first().click();
      const menu = page.getByRole('menuitem').first();
      if (await menu.isVisible().catch(() => false)) await menu.click();
      await waitIdle(page);
      const u = page.getByTestId('msg-user').last();
      await u.hover();
      await u.getByRole('button', { name: 'Edit message' }).click();
      await page.getByRole('textbox', { name: 'Edit message' }).fill(`${PROMPTS[(i + 1) % PROMPTS.length]}`);
      await page.getByRole('button', { name: 'Send', exact: true }).click();
      await waitIdle(page);
      samples.push(await sample(i));
    }
  }
  await expect(lastAssistant(page)).not.toBeEmpty();
  // Frames: scroll the thread top-to-bottom, then type in the composer.
  const frames = await page.evaluate(async () => {
    const el = [...document.querySelectorAll('main *')].find((e) => { const s = getComputedStyle(e); return /(auto|scroll)/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 100; }) as HTMLElement;
    const ft: number[] = []; let last = performance.now();
    await new Promise<void>((res) => {
      let y = el.scrollHeight;
      const step = () => { const now = performance.now(); ft.push(now - last); last = now; y -= 400; el.scrollTop = y; if (y > 0) requestAnimationFrame(step); else res(); };
      requestAnimationFrame(step);
    });
    ft.shift(); ft.sort((a, b) => a - b);
    return { n: ft.length, p50: +ft[Math.floor(ft.length / 2)].toFixed(1), p95: +ft[Math.floor(ft.length * 0.95)].toFixed(1), worst: +ft[ft.length - 1].toFixed(1), over50: ft.filter((x) => x > 50).length };
  });
  const probe = 'typing latency probe after fifty turns';
  const t0 = Date.now(); await composer(page).pressSequentially(probe, { delay: 0 }); const typeMs = +((Date.now() - t0) / probe.length).toFixed(1);
  const res = { samples, turnMs: { first10: Math.round(turnMs.slice(0, 10).reduce((a, b) => a + b) / 10), last10: Math.round(turnMs.slice(-10).reduce((a, b) => a + b) / 10) }, scroll: frames, typeMsPerKey: typeMs };
  fs.writeFileSync(process.env.SOAK_OUT ?? test.info().outputPath('soak.json'), JSON.stringify(res, null, 1));
  console.log(JSON.stringify(res));
  const first = samples[1], lastS = samples[samples.length - 1];
  expect(lastS.domNodes, 'DOM stays windowed (virtualized thread)').toBeLessThan(6000);
  expect(lastS.heapMB - first.heapMB, 'heap growth turn 10 -> 50 after GC').toBeLessThan(60);
  expect(lastS.docs, 'no leaked iframes/documents').toBeLessThan(10);
  expect(frames.p95, 'p95 scroll frame').toBeLessThan(50);
  expect(typeMs, 'typing ms/key').toBeLessThan(30);
});
