import { test, expect } from './fixtures';
import { MOCK, open, ask } from './helpers';

test('inline sidebar filter narrows chats by title @desktop', async ({ page }, info) => {
  test.setTimeout(120_000);
  await open(page, MOCK);
  const qs = ['Denial rate by payer', 'Top providers by paid amount', 'Aetna appeals backlog', 'Paid amount trend', 'Cascade Medicaid outliers', 'CARC 16 denials by month'];
  for (const q of qs) {
    await ask(page, q);
    await page.keyboard.press('Control+Shift+O');
  }
  const filter = page.getByTestId('sidebar-filter');
  await expect(filter).toBeVisible();
  await filter.fill('denial');
  await expect(page.getByTestId('thread-item')).toHaveCount(2);
  await page.getByTestId('sidebar').screenshot({ path: info.outputPath('sidebar-filter.png') });
  await filter.fill('nothing like this');
  await expect(page.getByTestId('filter-empty')).toBeVisible();
  await filter.press('Escape');
  await expect(page.getByTestId('thread-item')).toHaveCount(qs.length);
  await filter.fill('aetna');
  await filter.press('Enter');
  await expect(page.getByTestId('msg-user')).toContainText('Aetna appeals backlog');
});
