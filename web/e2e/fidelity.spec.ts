import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import { LIVE, open, ask, composer, sendBtn } from './helpers';

// Deep fidelity pass (C9 follow-up) against how Claude.ai and ChatGPT behave today (observed UI, never their source).
// Each assertion names its reference. gap() lines are ratchets for findings filed in backlog/<team>.md.

/** Sample an element's effective opacity on every animation frame for `ms`. */
const sampleOpacity = (page: Page, testSelector: string, ms: number) => page.evaluate(([sel, dur]) => new Promise<number[]>((res) => {
  const out: number[] = []; const t0 = performance.now();
  const tick = () => { const e = document.querySelector(sel as string); if (e) { let o = 1; for (let el: Element | null = e; el; el = el.parentElement) o *= +getComputedStyle(el).opacity; out.push(+o.toFixed(2)); }
    if (performance.now() - t0 < (dur as number)) requestAnimationFrame(tick); else res(out); };
  requestAnimationFrame(tick);
}), [testSelector, ms] as const);

test.describe('fidelity: composer send/stop (ChatGPT)', () => {
  test('send and stop swap in place: a disabled Send never flashes to full opacity (QA-S34, fixed)', async ({ page }) => {
    await open(page, LIVE);
    await composer(page).fill('What is the total paid amount?');
    await composer(page).press('Enter');
    await expect(page.getByRole('button', { name: 'Stop generating' })).toBeVisible();
    await expect(sendBtn(page)).toBeVisible({ timeout: 45_000 });
    // The answer just ended and the box is empty, so Send is disabled. ChatGPT swaps the icon in place with no pop.
    const frames = await sampleOpacity(page, 'button[aria-label="Send message"]', 300);
    expect(Math.max(...frames), `disabled Send opacity frames: ${frames.join(',')}`).toBeLessThan(0.5);
    const send = await sendBtn(page).boundingBox(); expect(send!.width).toBe(36); expect(send!.height).toBe(36);
  });
});

test.describe('fidelity: message action rows (ChatGPT/Claude)', () => {
  test('the latest answer keeps its actions visible; older answers reveal theirs on hover only', async ({ page }) => {
    test.setTimeout(120_000);
    await open(page, LIVE);
    await ask(page, 'What is the total paid amount?');
    await ask(page, 'What was our denial rate by payer last quarter?');
    await page.mouse.move(2, 2);
    const rows = page.getByTestId('msg-actions');
    await expect(rows).toHaveCount(2);
    const eff = (i: number) => rows.nth(i).evaluate((e) => { let o = 1; for (let el: Element | null = e; el; el = el.parentElement) o *= +getComputedStyle(el).opacity; return o; });
    await expect.poll(() => eff(0)).toBeLessThan(0.05);
    expect(await eff(1)).toBeGreaterThan(0.95);
    await page.getByTestId('msg-assistant').first().hover();
    await expect.poll(() => eff(0)).toBeGreaterThan(0.95);
    // Copy gives feedback by swapping its own icon and label (both references), not with a toast.
    await page.context().grantPermissions(['clipboard-read', 'clipboard-write']).catch(() => {});
    const copy = rows.nth(1).getByRole('button', { name: 'Copy', exact: true });
    await copy.click();
    await expect(rows.nth(1).getByRole('button', { name: 'Copied' })).toBeVisible();
    await expect(page.getByTestId('toast')).toHaveCount(0);
  });
});

test.describe('fidelity: thread chrome (ChatGPT)', () => {
  test('"Jump to latest" floats fully above the composer, centred on the column (QA-S32)', async ({ page }) => {
    test.setTimeout(120_000);
    await open(page, LIVE);
    for (const q of ['Show the paid amount trend by month', 'What was our denial rate by payer last quarter?']) await ask(page, q);
    await page.mouse.move(700, 300); await page.mouse.wheel(0, -5000);
    const jump = page.getByTestId('jump-latest');
    await expect(jump).toHaveCSS('opacity', '1');
    const j = (await jump.boundingBox())!;
    const c = (await page.locator('#composer form').first().boundingBox())!;
    expect(Math.abs(j.x + j.width / 2 - (c.x + c.width / 2))).toBeLessThanOrEqual(2);
    expect(c.y - (j.y + j.height), 'gap between the button and the composer top').toBeGreaterThanOrEqual(8);
  });

  test('sidebar: the active chat row is distinguishable from a hovered row (QA-S33)', async ({ page }) => {
    test.setTimeout(120_000);
    await open(page, LIVE);
    await ask(page, 'What is the total paid amount?');
    await page.getByRole('button', { name: 'New chat' }).first().click();
    await ask(page, 'Who are our top providers?');
    const items = page.getByTestId('thread-item');
    await expect.poll(() => items.count()).toBeGreaterThanOrEqual(2);
    const active = items.filter({ has: page.locator('[aria-current="page"]') }).first();
    const other = items.filter({ hasNot: page.locator('[aria-current="page"]') }).first();
    await other.hover(); await page.waitForTimeout(200);
    const bg = (l: typeof active) => l.evaluate((e) => getComputedStyle(e).backgroundColor);
    const [a, h] = [await bg(active), await bg(other)];
    expect(a, 'active row fill vs hovered row fill').not.toBe(h);
  });
});
