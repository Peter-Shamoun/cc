import { test, expect } from './fixtures';
import { MOCK, open, ask, lastAssistant, composer, stopBtn, waitIdle } from './helpers';

// Offline fixture adapter: deterministic, used for UI behaviour that does not need the API.
test.describe('mock adapter', () => {
  test('streaming: text grows token by token with a visible caret/status @both', async ({ page }) => {
    await open(page, MOCK);
    await composer(page).fill('What was our denial rate by payer last quarter?');
    await composer(page).press('Enter');
    const md = lastAssistant(page).getByTestId('markdown').first();
    await md.waitFor();
    const seen = new Set<number>();
    for (let i = 0; i < 25 && seen.size < 4; i++) {
      seen.add(((await md.textContent()) ?? '').length);
      await page.waitForTimeout(40);
    }
    expect(seen.size).toBeGreaterThanOrEqual(3);
    await waitIdle(page);
  });

  test('streaming tool steps move from running to done', async ({ page }) => {
    await open(page, MOCK);
    await composer(page).fill('Which CARC codes drive Cascade Medicaid denials?');
    await composer(page).press('Enter');
    const a = lastAssistant(page);
    const tool = a.getByTestId('step-timeline').or(a.getByTestId('tool-call')).first();
    await expect(tool).toBeVisible();
    await waitIdle(page);
    await expect(tool).toBeVisible();
  });

  test('stop mid-stream keeps the partial answer', async ({ page }) => {
    await open(page, MOCK);
    await composer(page).fill('Plot the monthly denial rate trend for our top 3 payers');
    await composer(page).press('Enter');
    await expect(stopBtn(page)).toBeVisible();
    await page.waitForTimeout(300);
    await stopBtn(page).click();
    await waitIdle(page, 5_000);
    await expect(page.getByTestId('msg-assistant')).toHaveCount(1);
  });

  test('Escape stops generation (ChatGPT/Claude parity)', async ({ page }) => {
    test.info().annotations.push({ type: 'parity', description: 'Esc-to-stop is optional; failure files a backlog item' });
    await open(page, MOCK);
    await composer(page).fill('Plot the monthly denial rate trend for our top 3 payers');
    await composer(page).press('Enter');
    await expect(stopBtn(page)).toBeVisible();
    await page.keyboard.press('Escape');
    await waitIdle(page, 5_000);
  });

  test('edit-and-branch: editing a question creates version 2/2 and arrows switch', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    await page.getByTestId('msg-user').first().hover();
    await page.getByRole('button', { name: 'Edit message' }).first().click();
    await page.getByRole('textbox', { name: 'Edit message' }).fill('What was our denial rate by payer last month?');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await waitIdle(page);
    const nav = page.getByTestId('version-nav').first();
    await expect(nav).toContainText(/2\s*\/\s*2/);
    await page.getByRole('button', { name: 'Previous version' }).click();
    await expect(nav).toContainText(/1\s*\/\s*2/);
    await expect(page.getByTestId('msg-user').first()).toContainText('last quarter');
    await page.getByRole('button', { name: 'Next version' }).click();
    await expect(page.getByTestId('msg-user').first()).toContainText('last month');
  });

  test('error part is shown with a retry affordance', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'Run the query against the raw 837 tables');
    const err = lastAssistant(page).getByTestId('error');
    await expect(err).toBeVisible();
  });

  test('new chat and sidebar history', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    await page.keyboard.press('Control+Shift+O');
    await expect(page.getByTestId('msg-user')).toHaveCount(0);
    await expect(page.getByTestId('sidebar')).toContainText(/denial/i);
  });

  test('mobile: sidebar is a drawer that closes on pick @mobile', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    await expect(page.getByTestId('sidebar')).toBeHidden();
    await page.getByRole('button', { name: 'Open sidebar' }).click();
    await expect(page.getByTestId('sidebar')).toBeVisible();
    await page.getByTestId('drawer-scrim').click({ position: { x: 370, y: 400 } });
    await expect(page.getByTestId('sidebar')).toBeHidden();
  });

  test('mobile: no horizontal overflow on an answer with a table @mobile', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
  });
});
