import { test, expect } from './fixtures';
import AxeBuilder from '@axe-core/playwright';
import { LIVE, MOCK, open } from './helpers';

const axe = async (page: import('@playwright/test').Page) =>
  (await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze()).violations.map((v) => v.id);

// Path routes (src/shell/routes.ts): unknown paths get a quiet 404, /signed-out covers sign-out and expiry.
test.describe('path routes @both', () => {
  test('an unknown deep link shows the 404, and its link goes home', async ({ page }) => {
    await page.goto(new URL('/c/does-not-exist/extra', MOCK).href);
    await expect(page.getByRole('heading', { level: 1, name: 'Page not found' })).toBeVisible();
    await expect(page).toHaveTitle('Page not found · ClaimClarity');
    expect(await axe(page)).toEqual([]);
    await page.getByRole('link', { name: 'Go to ClaimClarity' }).click();
    await expect(page.getByRole('textbox', { name: 'Message' })).toBeVisible();
    await page.goBack();
    await expect(page.getByTestId('not-found')).toBeVisible();
  });

  test('sharing is gone: /shared/<id> and /share/<id> are the normal 404 (Peter 12:15 AM)', async ({ page }) => {
    for (const path of ['/shared/sh_abc', '/share/sh_abc']) {
      await page.goto(new URL(path, MOCK).href);
      await expect(page.getByRole('heading', { level: 1, name: 'Page not found' })).toBeVisible();
      await expect(page.getByTestId('shared-view')).toHaveCount(0);
    }
  });

  test('the expired-session page is plain and accessible in dark mode too', async ({ page }) => {
    await open(page, MOCK, 'dark');
    await page.goto(new URL('/signed-out?reason=expired', MOCK).href);
    await expect(page.getByRole('heading', { name: 'Your session expired' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Sign in again' })).toHaveAttribute('href', '/');
    expect(await axe(page)).toEqual([]);
  });

  test('the offline mock has no session, so no Sign out', async ({ page, isMobile }) => {
    await open(page, MOCK);
    if (isMobile) await page.getByRole('button', { name: /open sidebar/i }).click();
    await page.getByTestId('account-button').click();
    await expect(page.getByRole('menuitem', { name: 'Settings' })).toBeVisible();
    await expect(page.getByRole('menuitem', { name: 'Sign out' })).toHaveCount(0);
  });
});

test.describe('sign out (live) @desktop', () => {
  test('account menu → Sign out calls the API and lands on the signed-out page', async ({ page }) => {
    await open(page, LIVE);
    const req = page.waitForRequest((r) => r.url().endsWith('/api/auth/sign-out') && r.method() === 'POST');
    await page.getByTestId('account-button').click();
    await page.getByRole('menuitem', { name: 'Sign out' }).click();
    const res = await (await req).response();
    expect(res?.status()).toBe(204);
    expect((await res!.allHeaders())['clear-site-data']).toContain('storage');
    await expect(page).toHaveURL(/\/signed-out$/);
    await expect(page.getByRole('heading', { name: 'You’re signed out' })).toBeVisible();
  });
});
