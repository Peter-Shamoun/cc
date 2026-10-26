import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import { LIVE, open, ask, freeze, lastAssistant, injectStream, contractExample, composer, sse, turn, textPart, isolateHistory } from './helpers';

/**
 * Visual baselines for every answer state, light + dark, 1440 (desktop) and 390 (mobile).
 * Streams are the canonical contract examples (api/contract/examples), injected on the live build,
 * so pixels depend only on the UI. Refresh with `npm run e2e:update` after reviewing diffs.
 */
const shot = async (page: Page, name: string, timer = false) => {
  await freeze(page);
  await page.mouse.move(0, 0);
  if (timer) {
    // Tag the running timer's element once; masking by text raced with the ticking value (timer text changes).
    await page.evaluate(() => { for (const el of document.querySelectorAll('span,div')) if (el.children.length === 0 && /^\d+(\.\d+)?\s?m?s$/.test((el.textContent || '').trim())) el.setAttribute('data-qa-timer', ''); });
  }
  await expect(page).toHaveScreenshot(`${name}.png`, { mask: [page.locator('time'), ...(timer ? [page.locator('[data-qa-timer]')] : [])], maxDiffPixelRatio: 0.002 });
};
const outcome = async (page: Page, name: string, prompt = 'Synthetic question') => {
  const ev = contractExample(name);
  test.skip(!ev, `no contract example ${name}`);
  await injectStream(page, ev!);
  await ask(page, prompt);
};

for (const theme of ['light', 'dark'] as const) {
  test.describe(`answer states ${theme} @both`, () => {
    test.beforeEach(async ({ page }) => { await isolateHistory(page); await open(page, LIVE, theme); });

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

    test(`thread: two exchanges ${theme}`, async ({ page }) => {
      // #288 turn rhythm: question→answer 24px, answer→next question 40px (Claude/ChatGPT exchange spacing).
      // Text-only turns: a chart's progressive render made the pixels unstable.
      await injectStream(page, (n) => turn(textPart('t', n ? 'Synthetic follow-up answer: denials fell 1.2 points quarter over quarter.' : 'Synthetic answer: the overall denial rate was 13.7% last quarter, highest for Payer A.')));
      await ask(page, 'Synthetic question');
      await ask(page, 'Synthetic follow-up');
      await expect(page.getByTestId('msg-assistant')).toHaveCount(2);
      const box = async (l: ReturnType<Page['getByTestId']>) => (await l.boundingBox())!;
      const [u1, a1, u2] = [await box(page.getByTestId('msg-user').first()), await box(page.getByTestId('msg-assistant').first()), await box(page.getByTestId('msg-user').nth(1))];
      const qa = a1.y - (u1.y + u1.height), aq = u2.y - (a1.y + a1.height);
      expect(aq - qa, `answer→question gap ${aq}px should be 16px more than question→answer ${qa}px`).toBeGreaterThanOrEqual(12);
      await page.getByTestId('thread-scroller').evaluate((el) => { el.scrollTop = 0; }); // integer offset: centring left sub-pixel drift
      await page.waitForTimeout(200);
      await shot(page, `thread-two-exchanges-${theme}`);
    });

    test(`edit inline ${theme}`, async ({ page }) => {
      await outcome(page, 'answer');
      await page.getByTestId('msg-user').first().hover();
      await page.getByRole('button', { name: 'Edit message' }).first().click();
      await shot(page, `state-edit-${theme}`);
    });
  });
}

/** Contract 1.16.0 edge scenarios (EDGE_SCENARIOS): recorded streams, light, 1440 + 390; dark for the busiest two. */
const EDGE = ['empty', 'multi_table', 'long_sql', 'many_repairs', 'clarify_nested', 'escalation_abstain',
  'cancel_plan', 'cancel_sql', 'cancel_repair', 'cancel_candidates', 'cancel_escalation', 'cancel_render'];
test.describe('edge scenarios @both', () => {
  test.beforeEach(async ({ page }) => { await isolateHistory(page); });
  for (const name of EDGE) {
    test(`edge ${name} light`, async ({ page }) => {
      await open(page, LIVE, 'light');
      await outcome(page, `edge/${name}`);
      await shot(page, `edge-${name}-light`);
    });
  }
  for (const name of ['many_repairs', 'cancel_escalation']) {
    test(`edge ${name} dark (steps open)`, async ({ page }) => {
      await open(page, LIVE, 'dark');
      await outcome(page, `edge/${name}`);
      const s = lastAssistant(page).getByTestId('step-summary').first();
      if ((await s.getAttribute('aria-expanded')) !== 'true') await s.click();
      await shot(page, `edge-${name}-dark-open`);
    });
  }
});
