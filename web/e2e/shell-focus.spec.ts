import { test, expect } from './fixtures';
import { MOCK, open, ask, composer } from './helpers';

// Focus management parity (ChatGPT/Claude): sending the first question swaps the centered composer for the
// docked one; typing focus must survive that, so a follow-up can be typed right away.
test.describe('focus (mock) @desktop', () => {
  test('the first send keeps focus in the composer through the answer (QA-S19: Firefox too)', async ({ page }) => {
    await open(page, MOCK);
    await expect(composer(page)).toBeFocused();
    await ask(page, 'Total paid by payer');
    await expect(composer(page)).toBeFocused();
    await page.keyboard.type('Who are our top providers?');
    await expect(composer(page)).toHaveValue('Who are our top providers?');
  });
});

test.describe('focus return (mock) @desktop', () => {
  test('Ctrl+K then Esc, and Settings from the account menu, give focus back instead of dropping it', async ({ page }) => {
    await open(page, MOCK);
    await composer(page).focus();
    await page.keyboard.press('ControlOrMeta+k');
    await expect(page.getByRole('textbox', { name: 'Search chats' }).or(page.getByRole('combobox', { name: 'Search chats' }))).toBeFocused();
    await page.keyboard.press('Escape');
    await expect(composer(page)).toBeFocused();
    await page.getByTestId('account-button').click();
    await page.getByRole('menuitem', { name: /Settings/ }).click();
    await expect(page.getByRole('dialog')).toBeVisible();
    await page.keyboard.press('Escape');
    await expect(composer(page)).toBeFocused();
  });
});

test.describe('focus on touch (mock) @mobile', () => {
  test('the composer does not grab focus on load or New chat (no surprise keyboard)', async ({ page }) => {
    await open(page, MOCK);
    await expect(composer(page)).toBeVisible();
    await expect(composer(page)).not.toBeFocused();
  });
});

test.describe('focus on chat open (mock) @desktop', () => {
  test('picking a chat in the sidebar puts focus in the composer, ready for a follow-up', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    await page.keyboard.press('Control+Shift+O');
    await expect(page.getByTestId('msg-user')).toHaveCount(0);
    await page.getByTestId('sidebar').getByRole('link', { name: /denial/i }).or(page.getByTestId('sidebar').getByRole('button', { name: /denial/i })).first().click();
    await expect(page.getByTestId('msg-user')).toHaveCount(1);
    await expect(composer(page)).toBeFocused();
  });
});

test.describe('chat open on touch (mock) @mobile', () => {
  test('picking a chat from the drawer does not raise the keyboard', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    await page.keyboard.press('Control+Shift+O');
    await expect(page.getByTestId('msg-user')).toHaveCount(0);
    await page.getByRole('button', { name: 'Open sidebar' }).click();
    await page.getByTestId('sidebar').getByRole('link', { name: /denial/i }).or(page.getByTestId('sidebar').getByRole('button', { name: /denial/i })).first().click();
    await expect(page.getByTestId('msg-user')).toHaveCount(1);
    await expect(page.getByTestId('sidebar')).toBeHidden();
    await expect(composer(page)).not.toBeFocused();
  });
});

test.describe('composer focus style (QA-S15) @desktop', () => {
  test('a soft border on click/auto focus, the strong ring only for keyboard navigation', async ({ page }) => {
    await open(page, MOCK);
    const box = page.locator('.cc-composer').first();
    await expect(composer(page)).toBeFocused();
    const ring = () => box.evaluate((el) => getComputedStyle(el).boxShadow);
    const focusColor = await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue('--focus').trim());
    expect(focusColor).not.toBe('');
    await expect.poll(ring).toMatch(/^rgba?\([^)]*\) 0px 0px 0px 1px, rgba\(0, 0, 0, 0\.08\) 0px 6px 20px 0px$/);
    const soft = await ring();
    await composer(page).click();
    expect(await ring()).toBe(soft);
    // Keyboard: Tab away and back in.
    await page.keyboard.press('Shift+Tab');
    await page.keyboard.press('Tab');
    await expect(composer(page)).toBeFocused();
    await expect.poll(ring).toMatch(/^rgba?\([^)]*\) 0px 0px 0px 2px, rgba\(0, 0, 0, 0\.08\) 0px 6px 20px 0px$/);
    // A click drops back to the soft style.
    await composer(page).click();
    await expect.poll(ring).toBe(soft);
  });
});
