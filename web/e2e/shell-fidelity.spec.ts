import { test, expect } from './fixtures';
import { MOCK, open, ask } from './helpers';

// QA C9 fidelity (docs/frontend-fidelity-audit.md): measured in the browser, not just the classes.
test.describe('fidelity (mock) @desktop', () => {
  test('S31 768px column, S30 1.75 icon stroke, S28 soft dialog shadow', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'Total paid by payer');
    const col = await page.getByTestId('thread-content').evaluate((el) => {
      const s = getComputedStyle(el);
      return el.clientWidth - parseFloat(s.paddingLeft) - parseFloat(s.paddingRight);
    });
    expect(col).toBe(768);
    const stroke = await page.locator('aside svg.lucide').first().evaluate((el) => getComputedStyle(el).strokeWidth);
    expect(stroke).toBe('1.75px');
    await page.keyboard.press('ControlOrMeta+,');
    const shadow = await page.getByRole('dialog').evaluate((el) => getComputedStyle(el).boxShadow);
    expect(shadow).toBe('rgba(0, 0, 0, 0.12) 0px 8px 32px 0px');
  });
});
