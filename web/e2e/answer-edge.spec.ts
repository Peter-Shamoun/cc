import { test, expect } from './fixtures';
import { LIVE, MOCK, open, ask, lastAssistant, composer, stopBtn, waitIdle, injectStream, turn, textPart, sse, contractExample } from './helpers';

// Answer-column edge cases and fidelity details (Peter 9:51 / 10:06 directives). Synthetic data only.
test.describe('answer edge cases', () => {
  test('reload mid-stream: one question, one finished answer, nothing stuck', async ({ page }) => {
    await open(page, LIVE);
    const q = `Total paid by payer (reload ${Date.now() % 100000})`;
    // Stop shows optimistically, before POST /api/chat is sent (#283/#285 timing). Reload only once the server
    // has accepted the run (response headers), otherwise the chat never existed and there is nothing to reopen.
    const accepted = page.waitForResponse((r) => r.url().includes('/api/chat') && r.request().method() === 'POST');
    await ask(page, q, { wait: false });
    await expect(stopBtn(page)).toBeVisible();
    await accepted;
    const id = new URL(page.url()).hash.match(/\/c\/([^/?]+)/)?.[1];
    expect(id, 'chat id in the URL').toBeTruthy();
    await page.reload();
    await expect(composer(page)).toBeVisible();
    // Reopen by id if the reload landed on a fresh screen (the sidebar title is the server's auto-title, not q).
    if (!(await page.getByTestId('msg-user').filter({ hasText: q }).count())) await page.goto(`${LIVE}#/c/${id}`);
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

  test('streaming pacing: a one-shot paragraph flows in over frames (ChatGPT/Claude), and lands complete', async ({ page }) => {
    await open(page, LIVE);
    const para = 'Cigna has the highest denial rate at 20.4%, followed by Medicaid at 20.3%. '.repeat(8).trim();
    await injectStream(page, turn(textPart('t', para)));
    await composer(page).fill('Synthetic question');
    const lengths = page.evaluate((full) => new Promise<number[]>((done) => {
      const seen: number[] = [];
      const t0 = performance.now();
      const tick = () => {
        const md = [...document.querySelectorAll('[data-testid="msg-assistant"] [data-testid="markdown"]')].at(-1);
        const n = md?.textContent?.length ?? 0;
        if (n && seen.at(-1) !== n) seen.push(n);
        // The action row must not show while the text is still flowing in (ChatGPT/Claude): -1 flags a violation.
        const row = [...document.querySelectorAll('[data-testid="msg-assistant"] [data-testid="msg-actions"]')].at(-1);
        if (n && n < full && row && getComputedStyle(row).visibility === 'visible') seen.push(-1);
        // ChatGPT hands-on: no caret; the trailing lines fade instead (-2: no fade mask mid-reveal, -3: a caret dot is drawn).
        if (n && n < full && md?.classList.contains('streaming-caret')) {
          const last = md.querySelector(':scope > div > div:last-child');
          if (!last || getComputedStyle(last).maskImage === 'none') seen.push(-2);
          const p = last?.querySelector('p:last-child');
          if (p && !['none', 'normal'].includes(getComputedStyle(p, '::after').content)) seen.push(-3);
        }
        if (performance.now() - t0 < 2500) requestAnimationFrame(tick); else done(seen);
      };
      requestAnimationFrame(tick);
    }), para.length);
    await composer(page).press('Enter');
    const seen = await lengths;
    expect(seen, 'action row visible mid-reveal').not.toContain(-1);
    expect(seen, 'trailing fade mask mid-reveal').not.toContain(-2);
    expect(seen, 'no streaming caret dot').not.toContain(-3);
    await expect(lastAssistant(page).getByTestId('msg-actions')).toBeVisible();
    expect(seen.length).toBeGreaterThan(5);
    expect([...seen].sort((a, b) => a - b)).toEqual(seen); // also proves no negative violation markers
    expect(seen.at(-1)).toBe(para.length);
    await expect(lastAssistant(page).locator('.streaming-caret')).toHaveCount(0);
    // Landed: uniform colour, no mask left on any block.
    expect(await lastAssistant(page).getByTestId('markdown').last().evaluate((md) => [...md.querySelectorAll(':scope > div > div')].every((d) => getComputedStyle(d).maskImage === 'none'))).toBe(true);
  });

  test('streaming pacing respects reduced motion: the paragraph appears at once', async ({ page }) => {
    const ev = contractExample('tool_steps');
    test.skip(!ev, 'no contract example tool_steps');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await open(page, LIVE);
    const para = 'Paid amount by payer, last quarter. '.repeat(12).trim();
    await injectStream(page, turn(textPart('t', para)));
    await ask(page, 'Synthetic question');
    await expect(lastAssistant(page).getByTestId('markdown')).toHaveText(para);
  });

  test('prose fidelity: nested bullets, h4, blockquote, rule, inline code and inline math (Claude/ChatGPT)', async ({ page }) => {
    await open(page, LIVE);
    const md = '#### Method\n\n- Payers\n  - Commercial\n    - Cigna\n\n> Denials exclude voided claims.\n\nAfter the note.\n\n---\n\nUse `is_latest_submission` and the rate $r = d/n$ per payer.';
    await injectStream(page, turn(textPart('t', md)));
    await ask(page, 'Synthetic question');
    const box = lastAssistant(page).getByTestId('markdown');
    await expect(box.locator('.katex')).toHaveCount(1, { timeout: 10_000 });
    const g = await box.evaluate((el) => {
      const cs = (sel: string) => getComputedStyle(el.querySelector(sel)!);
      const body = parseFloat(cs('p').fontSize);
      return {
        bullets: ['ul', 'ul ul', 'ul ul ul'].map((s) => cs(s).listStyleType),
        h4: { size: parseFloat(cs('h4').fontSize) / body, weight: Number(cs('h4').fontWeight) },
        quote: { bar: parseFloat(cs('blockquote').borderInlineStartWidth), below: parseFloat(cs('blockquote').marginBottom) },
        hr: { top: parseFloat(cs('hr').borderTopWidth), color: cs('hr').borderTopColor, text: getComputedStyle(el).color, margin: parseFloat(cs('hr').marginTop) },
        code: { mono: /mono/i.test(cs(':not(pre) > code').fontFamily), bg: cs(':not(pre) > code').backgroundColor },
      };
    });
    expect(g.bullets).toEqual(['disc', 'circle', 'square']);
    expect(g.h4.size).toBeCloseTo(1, 1);
    expect(g.h4.weight).toBeGreaterThanOrEqual(600);
    expect(g.quote.bar).toBe(2);
    expect(g.quote.below).toBeGreaterThan(0);
    expect(g.hr.top).toBe(1);
    expect(g.hr.color).not.toBe(g.hr.text); // a hairline in the border tone, not full-contrast text colour
    expect(g.hr.margin).toBeGreaterThanOrEqual(16);
    expect(g.code.bg).not.toBe('rgba(0, 0, 0, 0)');
  });

  test('thinking disclosure: collapsed one-line header while it runs, no spinner/timer, fade off under reduced motion, then "Thought for"', async ({ page }) => {
    const ev = contractExample('tool_steps');
    test.skip(!ev, 'no contract example tool_steps');
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await open(page, LIVE);
    await page.route('**/api/chat', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await new Promise((r) => setTimeout(r, 2500));
      await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: sse(ev!) }).catch(() => {});
    });
    await composer(page).fill('Synthetic question');
    await composer(page).press('Enter');
    const a = lastAssistant(page);
    const head = a.getByTestId('step-summary');
    await expect(head).toHaveAttribute('aria-expanded', 'false');
    await expect(a.getByTestId('step-activity')).toHaveText('Thinking');
    await expect(head.locator('.animate-spin')).toHaveCount(0);
    expect(await a.getByTestId('step-activity').evaluate((el) => getComputedStyle(el).animationName)).toBe('none');
    const t0 = await head.textContent();
    await page.waitForTimeout(1100);
    expect(await head.textContent()).toBe(t0); // nothing ticks
    await expect(a.getByTestId('step-timeline').locator('[aria-live]')).toHaveCount(0); // QA-S16: the shell status region announces
    await waitIdle(page);
    await expect(a.getByTestId('step-activity')).toHaveText(/^Thought for \d/);
    await expect(head).toHaveAttribute('aria-expanded', 'false');
    await head.click();
    await expect(head).toHaveAttribute('aria-expanded', 'true');
    await expect(a.getByTestId('step').first()).toBeVisible();
  });
});
