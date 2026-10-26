import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';
import { LIVE, open, ask, lastAssistant, composer, sse, turn, textPart } from './helpers';

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

  test('before the first event the turn shows a labelled "Thinking" row, not a bare dot (QA-A15)', async ({ page }) => {
    await page.route('**/api/chat', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await new Promise((r) => setTimeout(r, 2500)); // the server is slow to send its first event
      await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: sse(turn(textPart('t1', 'Done.'))) });
    });
    await open(page, LIVE);
    await composer(page).fill('What is the denial rate by payer?');
    await composer(page).press('Enter');
    // ChatGPT/Claude show one "Thinking" row from turn start; the same row later carries the timer and steps.
    await expect(lastAssistant(page)).toContainText(/thinking/i, { timeout: 1500 });
  });
});
