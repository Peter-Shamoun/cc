import { test, expect } from './fixtures';
import { MOCK, open } from './helpers';
import type { Page } from '@playwright/test';

/** A one-finger drag through CDP (real touch → pointer events with pointerType "touch"). */
async function drag(page: Page, from: [number, number], to: [number, number], steps = 8, ms = 16) {
  const cdp = await page.context().newCDPSession(page);
  const pt = (x: number, y: number) => [{ x, y, id: 1, radiusX: 2, radiusY: 2, force: 1 }];
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchStart', touchPoints: pt(...from) });
  for (let i = 1; i <= steps; i++) {
    const x = from[0] + ((to[0] - from[0]) * i) / steps, y = from[1] + ((to[1] - from[1]) * i) / steps;
    await cdp.send('Input.dispatchTouchEvent', { type: 'touchMove', touchPoints: pt(x, y) });
    await page.waitForTimeout(ms);
  }
  await cdp.send('Input.dispatchTouchEvent', { type: 'touchEnd', touchPoints: [] });
  await cdp.detach();
}

test.describe('phone drawer gestures @mobile', () => {
  test('swipe left closes the drawer; a short drag springs back; an edge swipe opens it', async ({ page, browserName }) => {
    test.skip(browserName !== 'chromium', 'real touch sequences are driven through CDP (Chromium only)');
    await open(page, MOCK);
    const sidebar = page.getByTestId('sidebar');
    await page.getByRole('button', { name: 'Open sidebar' }).click();
    await expect(sidebar).toBeVisible();
    await page.waitForTimeout(300); // open animation

    // Short, slow drag: springs back, stays open.
    await drag(page, [200, 400], [160, 404], 10, 40);
    await page.waitForTimeout(300);
    await expect(sidebar).toBeVisible();
    expect(await sidebar.evaluate((el) => el.style.transform)).toBe('');

    // A vertical drag is a scroll, not a close.
    await drag(page, [150, 600], [150, 300], 8, 16);
    await page.waitForTimeout(300);
    await expect(sidebar).toBeVisible();

    // Long swipe left: closes, focus goes back to the page.
    await drag(page, [250, 400], [20, 410]);
    await expect(sidebar).toBeHidden();
    await expect(page.getByTestId('drawer-scrim')).toHaveCount(0);

    // Swipe from the left edge: opens.
    await drag(page, [4, 400], [180, 405]);
    await expect(sidebar).toBeVisible();

    // Swiping on the scrim works too.
    await page.waitForTimeout(300);
    await drag(page, [370, 300], [80, 300]);
    await expect(sidebar).toBeHidden();
  });
});
