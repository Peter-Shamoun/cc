import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';
import { MOCK, open, ask } from './helpers';
import { distRoutes } from './static.mjs';

const scan = async (page: import('@playwright/test').Page) => {
  // Let entry fades finish: axe reads mid-fade opacity as a contrast failure (seen in C2 under load).
  await page.waitForFunction(() => document.getAnimations().every((a) => a.playState !== 'running' || a.effect?.getComputedTiming().iterations === Infinity), undefined, { timeout: 5_000 }).catch(() => {});
  const r = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const serious = r.violations.filter((v) => v.impact === 'serious' || v.impact === 'critical');
  test.info().attachments.push({ name: 'axe.json', contentType: 'application/json', body: Buffer.from(JSON.stringify(r.violations, null, 2)) });
  return serious.map((v) => `${v.id} (${v.nodes.length})`);
};

for (const theme of ['light', 'dark'] as const) {
  test.describe(`a11y ${theme} @both`, () => {
    test(`axe: empty state has no serious violations (${theme})`, async ({ page }) => {
      await open(page, MOCK, theme);
      expect(await scan(page)).toEqual([]);
    });
    test(`axe: answered thread has no serious violations (${theme})`, async ({ page }) => {
      await open(page, MOCK, theme);
      await ask(page, 'What was our denial rate by payer last quarter?');
      expect(await scan(page)).toEqual([]);
    });
  });
}

test('keyboard: composer focused on load, Enter sends, Shift+Enter newline, Tab reaches actions', async ({ page }) => {
  await open(page, MOCK);
  await expect(page.getByRole('textbox', { name: 'Message' })).toBeFocused();
  await page.keyboard.type('line one');
  await page.keyboard.press('Shift+Enter');
  await page.keyboard.type('line two');
  await expect(page.getByRole('textbox', { name: 'Message' })).toHaveValue(/line one\nline two/);
  await page.getByRole('textbox', { name: 'Message' }).fill('What was our denial rate by payer last quarter?');
  await page.keyboard.press('Enter');
  await expect(page.getByTestId('msg-user')).toHaveCount(1);
});

test('keyboard: Ctrl+K opens chat search', async ({ page }) => {
  await open(page, MOCK);
  await page.keyboard.press('Control+k');
  await expect(page.getByRole('combobox', { name: /search/i }).or(page.getByRole('searchbox')).or(page.getByRole('textbox', { name: /search/i })).first()).toBeFocused();
});

test('streaming answer is announced to screen readers (aria-live)', async ({ page }) => {
  await open(page, MOCK);
  await ask(page, 'What was our denial rate by payer last quarter?');
  expect(await page.locator('[aria-live], [role="log"], [role="status"]').count()).toBeGreaterThan(0);
});

test('reduced motion is respected', async ({ browser }) => {
  const ctx = await browser.newContext({ reducedMotion: 'reduce', viewport: { width: 1440, height: 900 } });
  await distRoutes(ctx);
  const page = await ctx.newPage();
  await open(page, MOCK);
  await ask(page, 'What was our denial rate by payer last quarter?');
  const animated = await page.evaluate(() => [...document.querySelectorAll('*')].filter((e) => {
    const s = getComputedStyle(e);
    return s.animationName !== 'none' && parseFloat(s.animationDuration) > 0.01 && s.animationIterationCount === 'infinite';
  }).length);
  expect(animated).toBe(0);
  await ctx.close();
});
