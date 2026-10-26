import fs from 'node:fs';
import type { CDPSession, Page } from '@playwright/test';
import { test, expect } from './fixtures';
import { LIVE, open, ask, contractExample, injectStream, type Ev } from './helpers';

/**
 * Platform memory probe: a 200-turn session of canned contract streams (answer, chart, tool steps, report),
 * so it runs in minutes and costs nothing. Every 25 turns: heap after GC, DOM nodes, JS listeners, documents.
 * Then the real leak test: leave the long chat for a new one and check DOM nodes (live + detached, after GC) and
 * listeners come back to baseline. MEM_SNAP=<prefix> also writes heap snapshots and counts detached DOM nodes.
 * Writes test-results/memory200.json (or $MEM_OUT).
 */
const KINDS = ['answer', 'chart', 'tool_steps', 'report'];

function streams(): Ev[][] {
  return KINDS.map((k) => contractExample(k)).filter((x): x is Ev[] => !!x);
}

async function detachedNodes(cdp: CDPSession): Promise<{ detached: number; top: [string, number][] }> {
  let raw = '';
  const onChunk = (e: { chunk: string }) => { raw += e.chunk; };
  cdp.on('HeapProfiler.addHeapSnapshotChunk', onChunk);
  await cdp.send('HeapProfiler.collectGarbage');
  await cdp.send('HeapProfiler.takeHeapSnapshot', { reportProgress: false });
  cdp.off('HeapProfiler.addHeapSnapshotChunk', onChunk);
  fs.writeFileSync(process.env.MEM_SNAP + Date.now() + '.heapsnapshot', raw);
  const snap = JSON.parse(raw) as { snapshot: { meta: { node_fields: string[] } }; nodes: number[]; strings: string[] };
  const f = snap.snapshot.meta.node_fields; const nameAt = f.indexOf('name'); const detAt = f.indexOf('detachedness'); const w = f.length;
  const counts = new Map<string, number>();
  for (let i = 0; i < snap.nodes.length; i += w) {
    const name = snap.strings[snap.nodes[i + nameAt]];
    // detachedness 2 = detached DOM (Chrome); older snapshots name such nodes "Detached <tag>".
    if ((detAt >= 0 && snap.nodes[i + detAt] === 2) || name.startsWith('Detached ')) counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  const detached = [...counts.values()].reduce((a, b) => a + b, 0);
  return { detached, top: [...counts].sort((a, b) => b[1] - a[1]).slice(0, 8) };
}

async function sampler(page: Page, cdp: CDPSession) {
  await cdp.send('Performance.enable');
  return async (turn: number) => {
    // Let React's passive effects and idle work settle, then two full GCs: Blink frees DOM wrappers over two cycles.
    await page.evaluate(() => new Promise<void>((r) => requestAnimationFrame(() => setTimeout(r, 50))));
    await cdp.send('HeapProfiler.collectGarbage');
    await cdp.send('HeapProfiler.collectGarbage');
    const { metrics } = await cdp.send('Performance.getMetrics');
    const m = Object.fromEntries(metrics.map((x) => [x.name, x.value]));
    const dom = await page.evaluate(() => document.querySelectorAll('*').length);
    return { turn, heapMB: +(m.JSHeapUsedSize / 1048576).toFixed(1), domNodes: dom, nodes: m.Nodes, listeners: m.JSEventListeners, docs: m.Documents };
  };
}

test('200-turn session: heap, DOM and listeners stay bounded, and leaving the chat frees it @perf', async ({ page }) => {
  test.setTimeout(1_500_000);
  const TURNS = Number(process.env.MEM_TURNS ?? 200);
  const S = streams();
  test.skip(S.length === 0, 'contract examples not published');
  await open(page, LIVE);
  await injectStream(page, (n) => {
    const evs = S[n % S.length];
    const id = `m_mem${n}`;
    return evs.map((e) => ('messageId' in e ? { ...e, messageId: id } : e));
  });
  const cdp = await page.context().newCDPSession(page);
  const sample = await sampler(page, cdp);
  const samples = [await sample(0)];
  for (let i = 1; i <= TURNS; i++) {
    await ask(page, `Question ${i} about denials`);
    if (i % 25 === 0) samples.push(await sample(i));
  }
  // Heap snapshots cost hundreds of MB on a shared box: only with MEM_SNAP=<path prefix> (for retainer hunting).
  const snap = process.env.MEM_SNAP ? () => detachedNodes(cdp) : async () => null;
  const inChat = await snap();
  // Leave the long chat for a new one: everything it rendered should become collectable.
  await page.keyboard.press('Control+Shift+O');
  await expect(page.getByTestId('msg-assistant')).toHaveCount(0);
  await page.waitForTimeout(500);
  const after = await sample(-1);
  const left = await snap();
  const res = { samples, after, detachedInChat: inChat, detachedAfterLeaving: left };
  fs.writeFileSync(process.env.MEM_OUT ?? test.info().outputPath('memory200.json'), JSON.stringify(res, null, 1));
  console.log(JSON.stringify(res));
  const at25 = samples[1]; const last = samples[samples.length - 1];
  // In the chat: windowed DOM, flat listeners, heap growth is message data only (~15 KB/turn measured).
  expect(last.domNodes, 'DOM stays windowed').toBeLessThan(6000);
  expect(last.listeners - at25.listeners, 'listeners do not grow per turn').toBeLessThan(50);
  expect((last.heapMB - at25.heapMB) / Math.max(1, last.turn - at25.turn), 'heap MB per turn after GC').toBeLessThan(0.08);
  // After leaving: the old thread is collectable. Vega keeps the last SVG it drew (one chart, < 700 nodes);
  // before the fixes a stale virtualizer closure, Vega's _el and a captured scroller kept 3k+ nodes and 140 listeners.
  expect(after.listeners - samples[0].listeners, 'listeners back to baseline').toBeLessThan(30);
  if (left) expect(left.detached, 'no detached thread DOM after leaving the chat').toBeLessThan(1000);
  expect(after.nodes - samples[0].nodes, 'DOM nodes (incl. detached) back near baseline').toBeLessThan(1500);
});
