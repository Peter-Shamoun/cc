import { test, expect } from './fixtures';
import { LIVE, open, ask } from './helpers';

// Share & export (contract 1.8.0 §15, links 1.11.0 §17): JSON from the server's export endpoint; org-only read-only links.
test.describe('share & export (live) @desktop', () => {
  test('the dialog downloads the server JSON export and prints only the conversation', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Total paid by payer');
    await page.getByTestId('share-button').click();
    const dlg = page.getByRole('dialog', { name: 'Share chat' });
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

test.describe('share links (live, contract 1.11.0) @desktop', () => {
  test('create a link (data off by default) → read-only /shared page → stop sharing → link is gone', async ({ page, context, browserName }) => {
    if (browserName === 'chromium') await context.grantPermissions(['clipboard-read', 'clipboard-write']);
    await open(page, LIVE);
    await ask(page, 'Total paid by payer');
    await page.getByTestId('share-button').click();
    const dlg = page.getByRole('dialog', { name: 'Share chat' });
    await expect(dlg.getByRole('switch', { name: 'Include result data' })).not.toBeChecked();
    await dlg.getByTestId('create-link').click();
    const url = await dlg.getByTestId('share-url').inputValue();
    expect(url).toMatch(/\/shared\/[^/]+$/);
    await expect(dlg.getByRole('status')).toContainText('Link copied');

    const viewer = await context.newPage();
    await viewer.goto(url);
    const view = viewer.getByTestId('shared-view');
    await expect(view.getByRole('heading', { level: 1 })).toBeVisible();
    await expect(view).toContainText('Shared within your organization · Read-only');
    await expect(view.getByTestId('shared-no-data')).toBeVisible();
    await expect(view.getByText('Total paid by payer').first()).toBeVisible();
    await expect(view.getByTestId('msg-assistant').first()).toBeVisible();
    await expect(viewer.getByRole('textbox', { name: 'Message' })).toHaveCount(0);
    await expect(viewer.getByRole('button', { name: /Regenerate/ })).toHaveCount(0);
    await expect(viewer.getByRole('button', { name: /Edit/ })).toHaveCount(0);
    await expect(viewer.getByTestId('msg-actions').first()).toBeHidden();
    await expect(viewer.getByRole('button', { name: 'Change' })).toHaveCount(0);
    await expect(viewer).toHaveTitle(/CC/);
    expect(new URL(viewer.url()).search).toBe('');

    await dlg.getByRole('button', { name: 'Stop sharing' }).click();
    await expect(dlg.getByTestId('create-link')).toBeVisible();
    await viewer.reload();
    await expect(viewer.getByTestId('shared-unavailable')).toContainText("This link isn't available");
    await viewer.close();
  });

  test('an unknown link reads as unavailable', async ({ page }) => {
    await page.goto(new URL('/shared/sh_doesnotexist', LIVE).href);
    await expect(page.getByTestId('shared-unavailable')).toContainText("This link isn't available");
    await expect(page.getByRole('link', { name: 'Go to CC' })).toHaveAttribute('href', '/');
  });
});
