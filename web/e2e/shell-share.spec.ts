import { test, expect } from './fixtures';
import { LIVE, open, ask } from './helpers';

// Share & export (contract 1.8.0 §15): link sharing is stubbed; JSON comes from the server's export endpoint.
test.describe('share & export (live) @desktop', () => {
  test('the dialog downloads the server JSON export and prints only the conversation', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Total paid by payer');
    await page.getByTestId('share-button').click();
    const dlg = page.getByRole('dialog', { name: 'Share chat' });
    await expect(dlg.getByTestId('create-link')).toHaveAttribute('aria-disabled', 'true');
    const dl = page.waitForEvent('download');
    await dlg.getByRole('button', { name: 'Download JSON' }).click();
    const file = await dl;
    expect(file.suggestedFilename()).toMatch(/\.json$/);
    const body = JSON.parse(await (await file.createReadStream()).toArray().then((c) => Buffer.concat(c).toString('utf8')));
    expect(JSON.stringify(body)).toContain('Total paid by payer');
    await page.keyboard.press('Escape');
    await page.emulateMedia({ media: 'print' });
    await expect(page.getByTestId('app-header')).toBeHidden();
    await expect(page.getByRole('textbox', { name: 'Message' })).toBeHidden();
    await expect(page.getByTestId('print-title')).toBeVisible();
    await expect(page.getByTestId('msg-assistant').last()).toBeVisible();
  });
});
