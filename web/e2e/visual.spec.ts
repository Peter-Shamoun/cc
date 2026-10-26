import { test, expect } from '@playwright/test';
import { MOCK, open, ask, freeze } from './helpers';

/**
 * Visual regression at 1440 (desktop project) and 390 (mobile project), light and dark.
 * Baselines live in e2e/__screenshots__; QA refreshes them each review cycle with
 * `npm run e2e:update` after the screens are reviewed.
 */
for (const theme of ['light', 'dark'] as const) {
  test.describe(`visual ${theme} @both`, () => {
    test(`empty state ${theme}`, async ({ page }) => {
      await open(page, MOCK, theme);
      await freeze(page);
      await expect(page).toHaveScreenshot(`empty-${theme}.png`);
    });
    test(`table answer ${theme}`, async ({ page }) => {
      await open(page, MOCK, theme);
      await ask(page, 'What was our denial rate by payer last quarter?');
      await freeze(page);
      await page.mouse.move(0, 0);
      await expect(page).toHaveScreenshot(`table-${theme}.png`, { mask: [page.locator('time')] });
    });
    test(`chart answer ${theme}`, async ({ page }) => {
      await open(page, MOCK, theme);
      await ask(page, 'Plot the monthly denial rate trend for our top 3 payers');
      await freeze(page);
      await page.mouse.move(0, 0);
      await expect(page).toHaveScreenshot(`chart-${theme}.png`, { mask: [page.locator('time')] });
    });
  });
}
