import { test, expect } from './fixtures';
import { MOCK, open, ask, composer } from './helpers';

// Focus management parity (ChatGPT/Claude): sending the first question swaps the centered composer for the
// docked one; typing focus must survive that, so a follow-up can be typed right away.
test.describe('focus (mock) @desktop', () => {
  test('the first send keeps focus in the composer through the answer', async ({ page }) => {
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
