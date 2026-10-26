import { test, expect } from './fixtures';
import { LIVE, open, composer, gap } from './helpers';

/**
 * Peter 11:57 PM directive: build to the backend design. The user never picks a database, lake, schema or view:
 * the session (Cognito → ccsim.query.open_session) fixes tenant + allowed relations, the analyzer discovers sem.*
 * itself (list_relations) and escalation mounts lossless views automatically. Each gap() flips red when it lands.
 */
test.describe('backend fit @both', () => {
  test('composer has no data-source / scope selector (QA-BF1)', async ({ page }) => {
    await open(page, LIVE);
    await expect(composer(page)).toBeVisible();
    gap('QA-BF1');
    const form = page.locator('#composer form');
    await expect(form.getByRole('button', { name: /lake|sem\.\*|scope|data source|database/i })).toHaveCount(0);
    await expect(page.getByText(/Claims lake/i)).toHaveCount(0);
  });

  test('product menu offers no scope or data-source choice (QA-BF2)', async ({ page }) => {
    await open(page, LIVE);
    await page.getByRole('button', { name: /^CC Analyst/ }).first().click();
    const menu = page.getByRole('menu').last();
    await expect(menu).toBeVisible();
    gap('QA-BF2');
    await expect(menu.locator('[role^="menuitem"]').filter({ hasText: /^Scope:|claims lake|^Data:/i })).toHaveCount(0);
  });
});
