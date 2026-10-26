import { test, expect } from './fixtures';
import { LIVE, open, injectStream, turn, textPart, composer, waitIdle } from './helpers';

/** QA-S13: browser find (Ctrl+F) reaches turns the long-thread virtualizer has scrolled out of the DOM. */
test('find-in-page reaches virtualized turns out of view @desktop', async ({ page }) => {
  test.setTimeout(240_000);
  await open(page, LIVE);
  await injectStream(page, (n) => turn(textPart(`t${n}`, `Answer ${n}: denials rose for synthetic payer zeta-${n}.`)));
  for (let i = 0; i < 20; i++) {
    await composer(page).fill(`Question ${i}: denial rate by payer?`);
    await composer(page).press('Enter');
    await waitIdle(page);
  }
  // The thread is virtualized: the first answer's full row is not mounted, only its find shadow.
  await expect(page.locator('[data-find-shadow]').first()).toBeAttached();
  const scroller = page.getByTestId('thread-scroller');
  const before = await scroller.evaluate((el) => el.scrollTop);
  expect(before).toBeGreaterThan(1000);
  // window.find is the scriptable form of Ctrl+F. It searches from the selection, so start clean.
  const found = await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    window.getSelection()?.removeAllRanges();
    return (window as unknown as { find: (s: string, cs: boolean, back: boolean, wrap: boolean) => boolean }).find('synthetic payer zeta-1.', false, true, true);
  });
  expect(found).toBe(true);
  // The match is in a shadow; native find then scrolls it into view (window.find doesn't, so do that step here).
  const where = await page.evaluate(() => {
    const el = window.getSelection()?.getRangeAt(0).startContainer.parentElement?.closest('[data-find-shadow]') as HTMLElement | null;
    el?.scrollIntoView({ block: 'center' });
    return el?.dataset.findShadow ?? null;
  });
  expect(where).not.toBeNull();
  // The window moves there and the full turn replaces its shadow.
  await expect.poll(() => scroller.evaluate((el) => el.scrollTop)).toBeLessThan(before - 500);
  await expect(page.getByTestId('msg-assistant').filter({ hasText: 'synthetic payer zeta-1.' })).toBeVisible();
  // Shadows stay out of the accessibility tree.
  expect(await page.locator('[data-find-shadow]:not([aria-hidden="true"])').count()).toBe(0);
});
