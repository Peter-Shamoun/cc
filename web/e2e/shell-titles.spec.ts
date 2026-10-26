import { test, expect } from './fixtures';
import { LIVE, open, ask } from './helpers';

// Contract 1.3.0: message.end.conversationTitle is the server's PHI-free title. The tab shows it as soon as
// the first answer ends, and a reload shows the same title (never the generic "New analysis").
// Desktop only: on phones the header has no 'Chat options' title button (QA, C5).
test.describe('chat titles (live)', () => {
  test('server title shows after the first answer and survives a reload', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Total paid by payer');
    const header = page.getByRole('button', { name: /^Chat options: / });
    await expect(header).not.toHaveAccessibleName(/New analysis/);
    const before = (await header.getAttribute('aria-label')) ?? '';
    await page.reload();
    await page.getByRole('link', { name: before.replace('Chat options: ', '') }).or(page.getByText(before.replace('Chat options: ', ''))).first().waitFor();
    await expect(page.getByText('New analysis')).toHaveCount(0);
  });
});
