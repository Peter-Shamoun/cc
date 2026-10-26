import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';
import { LIVE, open, ask, lastAssistant } from './helpers';

// SOTA-parity ratchets for open QA backlog items. Each gap() flips to "Expected to fail, but passed"
// when the owning team lands the fix; then delete the gap() line.
test.describe('parity gaps @both', () => {
  test('clarify question renders inline markdown, not raw asterisks', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'How bad are our denials?');
    const card = lastAssistant(page).getByTestId('clarify');
    await expect(card).toBeVisible();
    await expect(card).not.toContainText('**');
    await expect(card.locator('strong').first()).toBeAttached();
  });

  test('a thread with several answers has unique landmarks (axe landmark-unique)', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    await ask(page, 'Total paid by payer');
    const r = await new AxeBuilder({ page }).withRules(['landmark-unique']).analyze();
    expect(r.violations.map((v) => v.id)).toEqual([]);
  });
});
