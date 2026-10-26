import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import { LIVE, MOCK, open, ask, injectStream, turn, textPart } from './helpers';

/**
 * Print fidelity (Claude/ChatGPT): flat black-on-white, nothing clipped, no screen-only controls.
 * Chrome lays print out at the paper width without firing `resize`, so pixel-sized content (Vega charts) keeps its
 * screen width. We reproduce that by narrowing #root under print media without resizing the viewport.
 */
async function paper(page: Page, width = 620) {
  await page.evaluate(() => window.dispatchEvent(new Event('beforeprint')));
  await page.emulateMedia({ media: 'print' });
  await page.evaluate((w) => { document.getElementById('root')!.style.width = `${w}px`; }, width);
}

test.describe('print stylesheet @desktop', () => {
  test('a chart drawn at screen width is scaled to the paper column, not clipped', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'Plot the monthly denial rate trend for our top 3 payers');
    await expect(page.locator('[data-testid="chart"] svg.marks')).toBeVisible();
    await paper(page);
    const r = await page.evaluate(() => {
      const fig = document.querySelector('[data-testid="chart"]')!.getBoundingClientRect();
      const svg = document.querySelector('[data-testid="chart"] svg.marks')!.getBoundingClientRect();
      return { figRight: fig.right, svgRight: svg.right, ratio: svg.width / svg.height };
    });
    expect(r.svgRight).toBeLessThanOrEqual(r.figRight + 0.5);
    expect(r.ratio).toBeGreaterThan(1.5); // scaled proportionally (694x347 on screen), not squashed
  });

  test('long code lines wrap; no shadows, sticky headers or screen-only answer controls on paper', async ({ page }) => {
    const longLine = 'SELECT ' + Array.from({ length: 40 }, (_, i) => `col_${i}`).join(', ') + ' FROM sem.claim_outcomes';
    await injectStream(page, turn(textPart('t1', `Here is the query:\n\n\`\`\`sql\n${longLine}\n\`\`\`\n`)));
    await open(page, LIVE);
    await ask(page, 'Show me the query');
    await paper(page);
    const r = await page.evaluate(() => {
      const pre = document.querySelector('.prose-chat pre') as HTMLElement;
      const vis = (e: Element) => { const b = e.getBoundingClientRect(); return b.width > 0 && b.height > 0 && getComputedStyle(e).display !== 'none'; };
      const all = [...document.querySelectorAll('body *')].filter(vis);
      return {
        preClipped: pre.scrollWidth > pre.clientWidth + 1,
        preRight: pre.getBoundingClientRect().right,
        rootRight: document.getElementById('root')!.getBoundingClientRect().right,
        shadows: all.filter((e) => getComputedStyle(e).boxShadow !== 'none').length,
        sticky: all.filter((e) => getComputedStyle(e).position === 'sticky' || getComputedStyle(e).position === 'fixed').length,
        controls: all.filter((e) => e.matches('[data-testid="msg-assistant"] button[aria-label^="Copy"], [data-testid="msg-assistant"] button[aria-label^="Download"], [data-print-hide], [data-testid="user-actions"]')).length,
      };
    });
    expect(r.preClipped).toBe(false);
    expect(r.preRight).toBeLessThanOrEqual(r.rootRight + 0.5);
    expect(r.shadows).toBe(0);
    expect(r.sticky).toBe(0);
    expect(r.controls).toBe(0);
  });

  test('dark mode prints light, and returns to dark after printing', async ({ page }) => {
    await open(page, MOCK, 'dark');
    await ask(page, 'Total paid by payer');
    await expect(page.locator('html')).toHaveClass(/dark/);
    await paper(page);
    expect(await page.evaluate(() => getComputedStyle(document.body).backgroundColor)).toBe('rgb(255, 255, 255)');
    await expect(page.locator('html')).not.toHaveClass(/dark/);
    await page.evaluate(() => window.dispatchEvent(new Event('afterprint')));
    await expect(page.locator('html')).toHaveClass(/dark/);
  });
});
