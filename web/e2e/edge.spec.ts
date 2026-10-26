import { test, expect } from './fixtures';
import { LIVE, open, ask, lastAssistant, composer, waitIdle } from './helpers';

// Error and edge states that ChatGPT/Claude handle visibly: going offline, server rate limits.
test.describe('edge states (live) @both', () => {
  test('offline: banner shows, sending pauses, and recovers on reconnect', async ({ page, context }) => {
    await open(page, LIVE);
    await context.setOffline(true);
    await page.evaluate(() => window.dispatchEvent(new Event('offline')));
    await expect(page.getByText(/you.re offline/i)).toBeVisible();
    await context.setOffline(false);
    await page.evaluate(() => window.dispatchEvent(new Event('online')));
    await expect(page.getByText(/you.re offline/i)).toHaveCount(0);
    await ask(page, 'Total paid by payer');
    await expect(lastAssistant(page).getByTestId('data-table').first()).toBeVisible();
  });

  test('429 before the answer starts is retried on its own (no error card)', async ({ page }) => {
    let hits = 0;
    await page.route('**/api/chat', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      hits += 1;
      if (hits === 1) return route.fulfill({ status: 429, headers: { 'Retry-After': '1', 'content-type': 'application/json' }, body: JSON.stringify({ error: { code: 'rate_limited', message: 'Too many runs' } }) });
      return route.continue();
    });
    await open(page, LIVE);
    await composer(page).fill('Total paid by payer');
    await composer(page).press('Enter');
    await waitIdle(page, 30_000);
    expect(hits).toBeGreaterThanOrEqual(2);
    await expect(lastAssistant(page).getByTestId('data-table').first()).toBeVisible();
    await expect(lastAssistant(page).getByTestId('error')).toHaveCount(0);
  });
});
