import { test, expect } from './fixtures';
import { LIVE, open, ask } from './helpers';

// Contract 1.4.0: the product menu lists GET /api/capabilities; the pick rides on ChatRequest.options and the
// demo server validates it (a bad id would be a 422), so a full answer means the options were accepted.
test.describe('strategy picker (live) @desktop', () => {
  test('picking Cross-check sends options.strategy and the answer still completes', async ({ page }) => {
    await open(page, LIVE);
    await page.getByTestId('product-menu').click();
    await expect(page.getByRole('menuitemradio', { name: /Standard/ })).toHaveAttribute('aria-checked', 'true');
    const sent = page.waitForRequest((r) => r.method() === 'POST' && /\/api\/chat/.test(r.url()));
    await page.getByRole('menuitemradio', { name: /Cross-check/ }).click();
    await expect(page.getByTestId('product-menu')).toHaveAccessibleName(/Cross-check/);
    await ask(page, 'Total paid by payer');
    expect((await sent).postDataJSON().options).toEqual({ strategy: 'candidates' });
  });
});
