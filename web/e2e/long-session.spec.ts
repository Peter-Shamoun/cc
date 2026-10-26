import { test, expect } from './fixtures';
import type { Route } from '@playwright/test';
import { LIVE, open, ask, lastAssistant, waitIdle } from './helpers';

/**
 * 200+ turn sessions without asking 200 questions: two real demo turns are stored, then the stored transcript is
 * served back inflated to 220 turns (synthetic copies, chained parentIds). Checks open time, windowed DOM, heap,
 * reaching the first and last turn, and that a streaming answer never yanks a reader who scrolled up.
 */
const TURNS = 220;

test('220-turn chat: opens fast, stays windowed, first/last turn reachable, no yank while streaming', async ({ page }) => {
  test.setTimeout(240_000);
  await open(page, LIVE);
  await ask(page, 'Total paid by payer');
  await ask(page, 'What is the denial rate by payer?');
  const id = decodeURIComponent(/#\/c\/([^/?]+)/.exec(page.url())![1]);
  let inflate = true;
  await page.route(`**/api/conversations/${encodeURIComponent(id)}`, async (route: Route) => {
    if (route.request().method() !== 'GET' || !inflate) return route.fallback();
    const res = await route.fetch();
    const body = await res.json();
    const base = body.messages as { id: string; parentId?: string; text?: string; role: string }[];
    const pairs = base.length / 2;
    const out: typeof base = [];
    let prev: string | undefined;
    for (let k = 0; k < TURNS / pairs; k++) {
      for (const m of base) {
        const nid = `${m.id}-k${k}`;
        out.push({ ...m, id: nid, parentId: prev, ...(m.role === 'user' ? { text: `${m.text} #${k}` } : {}) });
        prev = nid;
      }
    }
    await route.fulfill({ response: res, json: { ...body, messages: out } });
  });
  const t0 = Date.now();
  await page.reload();
  await expect(lastAssistant(page)).toBeVisible();
  await expect(page.getByTestId('msg-user').filter({ hasText: `#${TURNS / 2 - 1}` }).last()).toBeVisible();
  const openMs = Date.now() - t0;
  const dom = await page.evaluate(() => ({ nodes: document.querySelectorAll('*').length, users: document.querySelectorAll('[data-testid="msg-user"]').length }));
  expect(openMs, 'open a 220-turn chat').toBeLessThan(8000);
  expect(dom.users, 'only a window of turns is mounted').toBeLessThan(60);
  expect(dom.nodes, 'DOM stays bounded').toBeLessThan(8000);
  const heap = await page.evaluate(() => (performance as unknown as { memory?: { usedJSHeapSize: number } }).memory?.usedJSHeapSize ?? 0);
  if (heap) expect(heap / 1048576, 'heap MB with 220 turns').toBeLessThan(250);

  // The first turn is reachable (scroll to top; the virtualizer mounts it).
  const sc = page.getByTestId('thread-scroller');
  for (let i = 0; i < 40 && !(await page.getByTestId('msg-user').filter({ hasText: /#0$/ }).count()); i++) {
    await sc.evaluate((el) => { el.scrollTop = 0; el.dispatchEvent(new WheelEvent('wheel', { deltaY: -1000, bubbles: true })); });
    await page.waitForTimeout(100);
  }
  await expect(page.getByTestId('msg-user').filter({ hasText: /#0$/ }).first()).toBeVisible();

  // Back to the bottom, ask live; mid-stream the reader scrolls up and the viewport must hold still.
  inflate = false;
  await sc.evaluate((el) => { el.scrollTop = el.scrollHeight; });
  await ask(page, 'Plot the monthly denial rate trend for our top 3 payers', { wait: false });
  await expect(page.getByRole('button', { name: /^stop/i })).toBeVisible();
  await page.mouse.move(700, 300);
  await page.mouse.wheel(0, -1500);
  await page.waitForTimeout(300);
  const tops: number[] = [];
  for (let i = 0; i < 12; i++) { tops.push(await sc.evaluate((el) => el.scrollTop)); await page.waitForTimeout(120); }
  expect(Math.max(...tops) - Math.min(...tops), `scrollTop held while streaming: ${tops.join(',')}`).toBeLessThanOrEqual(4);
  await waitIdle(page);
  expect(await page.evaluate(() => document.querySelectorAll('[data-testid="msg-user"]').length)).toBeLessThan(60);
});
