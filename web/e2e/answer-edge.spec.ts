import { test, expect } from './fixtures';
import { LIVE, MOCK, open, ask, lastAssistant, composer, stopBtn, waitIdle } from './helpers';

// Answer-column edge cases and fidelity details (Peter 9:51 / 10:06 directives). Synthetic data only.
test.describe('answer edge cases', () => {
  test('reload mid-stream: one question, one finished answer, nothing stuck', async ({ page }) => {
    await open(page, LIVE);
    const q = `Total paid by payer (reload ${Date.now() % 100000})`;
    await ask(page, q, { wait: false });
    await expect(stopBtn(page)).toBeVisible();
    await page.reload();
    await expect(composer(page)).toBeVisible();
    // Reopen the chat if the reload landed on a fresh screen.
    if (!(await page.getByTestId('msg-user').filter({ hasText: q }).count())) await page.getByRole('navigation', { name: 'Chats' }).getByRole('button', { name: q, exact: true }).click();
    await waitIdle(page);
    await expect(page.getByTestId('msg-user').filter({ hasText: q })).toHaveCount(1);
    await expect(page.getByTestId('msg-assistant')).toHaveCount(1);
    // Never a turn stuck "streaming". The server cancelled the orphaned run, so the chat says so and Retry answers.
    await expect(lastAssistant(page).locator('.streaming-caret')).toHaveCount(0);
    await expect(lastAssistant(page)).toContainText('Response interrupted');
    await lastAssistant(page).getByRole('button', { name: /Retry/ }).first().click();
    await waitIdle(page);
    await expect(page.getByTestId('msg-user').filter({ hasText: q })).toHaveCount(1);
    await expect(page.getByTestId('msg-assistant')).toHaveCount(1);
    await expect(lastAssistant(page)).not.toContainText('Response interrupted');
  });

  test('200% zoom (640px CSS viewport): no page-level horizontal scroll; table scrolls in its frame', async ({ page }) => {
    await page.setViewportSize({ width: 640, height: 450 });
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    const t = lastAssistant(page).getByTestId('data-table').first();
    await expect(t).toBeVisible();
    const box = (await t.boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(640);
    await expect(composer(page)).toBeInViewport();
  });

  test('streaming blocks fade in once each; nothing re-fades or keeps animating after the end', async ({ page }) => {
    await open(page, MOCK);
    await page.evaluate(() => {
      const w = window as unknown as { __fades: number; __refades: number; __seen: WeakSet<Element> };
      w.__fades = 0; w.__refades = 0; w.__seen = new WeakSet();
      document.addEventListener('animationstart', (e) => {
        const el = e.target as Element;
        if (e.animationName !== 'cc-fade' || !el.closest('[data-testid="markdown"]')) return;
        w.__fades++;
        if (w.__seen.has(el)) w.__refades++;
        w.__seen.add(el);
      }, true);
    });
    await ask(page, 'What was our denial rate by payer last quarter?');
    const { fades, refades } = await page.evaluate(() => { const w = window as unknown as { __fades: number; __refades: number }; return { fades: w.__fades, refades: w.__refades }; });
    expect(fades).toBeGreaterThan(0);
    // Each block fades in once as it appears; an element that re-fades means content flashing mid-stream.
    expect(refades).toBe(0);
    // After the end nothing in the answer is still animating.
    const animating = await lastAssistant(page).locator('[data-testid="markdown"] *').evaluateAll((els) => els.filter((el) => getComputedStyle(el).animationName === 'cc-fade').length);
    expect(animating).toBe(0);
  });

  test('answer links: quiet underline that firms up on hover', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    const color = await lastAssistant(page).locator('.prose-chat').first().evaluate((root) => {
      const a = document.createElement('a'); a.href = '#x'; a.textContent = 'synthetic link'; a.id = 'probe-link';
      root.appendChild(a);
      const cs = getComputedStyle(a);
      return { line: cs.textDecorationLine, deco: cs.textDecorationColor, color: cs.color, thick: cs.textDecorationThickness };
    });
    expect(color.line).toContain('underline');
    expect(color.thick).toBe('1px');
    expect(color.deco).not.toBe(color.color);
    await page.hover('#probe-link');
    await expect.poll(() => page.$eval('#probe-link', (a) => { const cs = getComputedStyle(a); return cs.textDecorationColor === cs.color; })).toBe(true);
  });

  test('code block: long lines scroll inside the block, never the page; Wrap lines wraps them', async ({ page }) => {
    await page.setViewportSize({ width: 800, height: 800 });
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    await lastAssistant(page).getByRole('button', { name: /View SQL/i }).first().click();
    const block = lastAssistant(page).getByTestId('code-block').first();
    await expect(block).toBeVisible();
    // Make one line very long (synthetic) to probe the layout.
    await block.locator('pre').first().evaluate((pre) => { pre.textContent = `SELECT ${'payer_name_column, '.repeat(40)}1 FROM claims`; });
    const body = block.locator('.shiki-wrap');
    const m = await body.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
    expect(m.sw).toBeGreaterThan(m.cw);
    expect(await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth)).toBeLessThanOrEqual(0);
    await block.hover();
    await block.getByRole('button', { name: 'Wrap lines' }).click();
    const w = await body.evaluate((el) => ({ sw: el.scrollWidth, cw: el.clientWidth }));
    expect(w.sw).toBeLessThanOrEqual(w.cw + 1);
  });

  test('turn spacing: question → answer and answer → next question', async ({ page }) => {
    await open(page, MOCK);
    await ask(page, 'What was our denial rate by payer last quarter?');
    await ask(page, 'Total paid by payer');
    const g = await page.evaluate(() => {
      const us = [...document.querySelectorAll('[data-testid="msg-user"]')];
      const as = [...document.querySelectorAll('[data-testid="msg-assistant"]')];
      const r = (e: Element) => e.getBoundingClientRect();
      return { qToA: Math.round(r(as[0]).top - r(us[0]).bottom), aToQ: Math.round(r(us[1]).top - r(as[0]).bottom) };
    });
    console.log('turn spacing', JSON.stringify(g));
    expect(g.qToA).toBeGreaterThanOrEqual(12);
    expect(g.qToA).toBeLessThanOrEqual(32);
    expect(g.aToQ).toBeGreaterThanOrEqual(24);
    expect(g.aToQ).toBeLessThanOrEqual(56);
  });
});
