import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import { LIVE, open, ask, lastAssistant, gap } from './helpers';

/**
 * Peter 12:15 AM: sharing is removed entirely. No share affordance anywhere (header, menus, keyboard, mobile,
 * settings) and the share routes are plain 404s. gap('QA-SH0') flips to "expected to fail but passed" once
 * shell + contract's removal PRs merge; QA then deletes the gap() lines.
 */
const SHARE = /\bshare|shared link/i;
const noShareVisible = async (page: Page, where: string) => {
  const hits = page.locator('button:visible, a:visible, [role^="menuitem"]:visible, [role="option"]:visible').filter({ hasText: SHARE });
  const named = page.getByRole('button', { name: SHARE });
  expect(await hits.count() + (await named.filter({ visible: true }).count()), `share affordance in ${where}`).toBe(0);
};
const escape = (page: Page) => page.keyboard.press('Escape');

test.describe('sharing removed @both', () => {
  test('no share affordance in the header, menus, shortcuts or settings (QA-SH0)', async ({ page, isMobile }) => {
    // QA-SH0 UI half verified fixed by #302 (C10-final): ratchet removed, now a regression guard.
    await open(page, LIVE);
    await ask(page, 'What was our denial rate by payer last quarter?');
    await noShareVisible(page, 'the chat view');
    // header ⋯ (conversation menu)
    const more = page.getByRole('button', { name: /^(More|Conversation options|Chat actions)/i }).first();
    if (await more.isVisible().catch(() => false)) { await more.click(); await noShareVisible(page, 'the header menu'); await escape(page); }
    // the answer's More menu
    await lastAssistant(page).hover();
    await lastAssistant(page).getByRole('button', { name: 'More actions' }).last().click();
    await noShareVisible(page, "the answer's More menu"); await escape(page);
    // sidebar row ⋯ menu (mobile: open the drawer first)
    if (isMobile) await page.getByRole('button', { name: 'Open sidebar' }).click();
    await page.getByRole('button', { name: /^(Chat options|Options for)/ }).first().click();
    await noShareVisible(page, 'the chat row menu'); await escape(page);
    // keyboard: the shortcuts sheet lists no share binding
    if (!isMobile) {
      await page.keyboard.press('Control+/');
      const sheet = page.getByTestId('shortcuts-dialog');
      if (await sheet.isVisible().catch(() => false)) { await expect(sheet).not.toContainText(SHARE); await escape(page); }
    }
  });

  test('settings has no shared-links section (QA-SH0, already true)', async ({ page, isMobile }) => {
    await open(page, LIVE);
    if (isMobile) await page.getByRole('button', { name: 'Open sidebar' }).click();
    await page.getByRole('button', { name: 'Demo user' }).first().click().catch(() => {});
    await page.getByRole('menuitem', { name: /Settings/ }).click();
    const dlg = page.getByTestId('settings-dialog');
    await expect(dlg).toBeVisible();
    for (const tab of await dlg.getByRole('tab').all()) { await tab.click(); await expect(dlg).not.toContainText(SHARE); }
  });

  test('share routes are plain 404s (QA-SH0)', async ({ page }) => {
    gap('QA-SH0');
    await open(page, LIVE);
    await ask(page, 'Total paid by payer');
    const id = new URL(page.url()).hash.match(/\/c\/([^/?]+)/)?.[1];
    expect(id).toBeTruthy();
    const headers = { authorization: 'Bearer demo:acme_labs:qa', 'content-type': 'application/json' };
    const put = await page.request.put(`${LIVE}api/conversations/${id}/share`, { headers, data: { includeData: false } });
    expect(put.status(), 'PUT /api/conversations/{id}/share').toBe(404);
    expect((await page.request.get(`${LIVE}api/shared/sh_qa_probe`, { headers })).status()).toBe(404);
    await page.goto(`${LIVE}shared/sh_qa_probe`);
    await expect(page.getByText(/not found|doesn.t exist|page you/i).first()).toBeVisible();
    await expect(page.getByTestId('shared-view')).toHaveCount(0);
  });
});
