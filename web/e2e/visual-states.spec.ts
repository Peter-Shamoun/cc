import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import { LIVE, open, ask, freeze, lastAssistant, injectStream, contractExample, composer, sse, turn, textPart } from './helpers';

/**
 * Visual baselines for every answer state, light + dark, 1440 (desktop) and 390 (mobile).
 * Streams are the canonical contract examples (api/contract/examples), injected on the live build,
 * so pixels depend only on the UI. Refresh with `npm run e2e:update` after reviewing diffs.
 */
const shot = async (page: Page, name: string, timer = false) => {
  await freeze(page);
  await page.mouse.move(0, 0);
  await expect(page).toHaveScreenshot(`${name}.png`, { mask: [page.locator('time'), ...(timer ? [page.getByText(/^\d+(\.\d+)?\s?m?s$/)] : [])], maxDiffPixelRatio: 0.002 });
};
const outcome = async (page: Page, name: string, prompt = 'Synthetic question') => {
  const ev = contractExample(name);
  test.skip(!ev, `no contract example ${name}`);
  await injectStream(page, ev!);
  await ask(page, prompt);
};

for (const theme of ['light', 'dark'] as const) {
  test.describe(`answer states ${theme} @both`, () => {
    test.beforeEach(async ({ page }) => { await open(page, LIVE, theme); });

    for (const name of ['answer', 'chart', 'clarify', 'abstain', 'escalation', 'error', 'report', 'candidates']) {
      test(`${name} ${theme}`, async ({ page }) => {
        await outcome(page, name);
        await shot(page, `state-${name}-${theme}`);
      });
    }

    test(`tool steps expanded ${theme}`, async ({ page }) => {
      await outcome(page, 'tool_steps');
      const s = lastAssistant(page).getByTestId('step-summary').first();
      if ((await s.getAttribute('aria-expanded')) !== 'true') await s.click();
      await shot(page, `state-steps-open-${theme}`);
    });

    test(`thinking before first event ${theme}`, async ({ page }) => {
      await page.route('**/api/chat', async (route) => {
        if (route.request().method() !== 'POST') return route.continue();
        await new Promise((r) => setTimeout(r, 8000));
        await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: sse(turn(textPart('t', 'ok'))) }).catch(() => {});
      });
      await composer(page).fill('Synthetic question');
      await composer(page).press('Enter');
      await page.waitForTimeout(600);
      await expect(page.getByText(/^\d+(\.\d+)?\s?m?s$/).first()).toBeVisible();
      await shot(page, `state-thinking-${theme}`, true); // the running timer is masked
    });

    test(`stopped ${theme}`, async ({ page }) => {
      await injectStream(page, turn(textPart('t', 'Denial rate was 13.7% overall. The highest'), { end: { finishReason: 'aborted' } }));
      await ask(page, 'Synthetic question');
      await shot(page, `state-stopped-${theme}`);
    });

    test(`feedback panel ${theme}`, async ({ page }) => {
      await outcome(page, 'answer');
      await lastAssistant(page).getByRole('button', { name: 'Bad response' }).click();
      await expect(lastAssistant(page).getByTestId('feedback-panel')).toBeVisible();
      await shot(page, `state-feedback-${theme}`);
    });

    test(`edit inline ${theme}`, async ({ page }) => {
      await outcome(page, 'answer');
      await page.getByTestId('msg-user').first().hover();
      await page.getByRole('button', { name: 'Edit message' }).first().click();
      await shot(page, `state-edit-${theme}`);
    });
  });
}
