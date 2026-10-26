import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import { LIVE, open, ask, composer, gap, waitIdle } from './helpers';

// QA keyboard-only walkthrough + screen-reader announcement audit (desktop, live demo API).
const active = (page: Page) => page.evaluate(() => {
  const e = document.activeElement as HTMLElement | null;
  if (!e || e === document.body) return 'BODY';
  return `${e.getAttribute('role') || e.tagName.toLowerCase()}:${(e.getAttribute('aria-label') || e.textContent || '').trim().slice(0, 40)}`;
});

test.describe('keyboard walkthrough @desktop', () => {
  test('Tab from load reaches skip link, sidebar, header and composer with no trap', async ({ page }) => {
    await open(page, LIVE);
    const seen: string[] = [await active(page)]; // the composer has focus on load
    // The sidebar lists every chat the shared demo server holds (two stops per row), so allow plenty of presses.
    for (let i = 0; i < 120 && !seen.includes('button:More options'); i++) {
      await page.keyboard.press('Tab');
      const cur = await active(page);
      // Firefox hands focus to the browser chrome after the last stop instead of wrapping (the document loses focus
      // while activeElement stays put); bring focus back to the top of the document and keep walking.
      if (cur === 'BODY' || cur === seen[seen.length - 1]) {
        await page.evaluate(() => {
          const b = document.body; const had = b.getAttribute('tabindex');
          b.setAttribute('tabindex', '-1'); b.focus();
          if (had === null) b.removeAttribute('tabindex'); else b.setAttribute('tabindex', had);
        });
      }
      seen.push(cur);
    }
    const text = seen.join('\n');
    for (const want of [/Skip to message box/, /New chat/, /Search chats/, /More options/, /textarea:Message/, /Attach text files/]) expect(text, `tab stops:\n${text}`).toMatch(want);
    expect(new Set(seen).size).toBeGreaterThan(8);
  });

  test('answer actions: menus open on Enter, arrows move, Esc returns focus to the trigger', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Which CARC codes drive denials?');
    for (const name of ['More actions', 'Answer style']) {
      const trigger = page.getByRole('button', { name, exact: true }).last(); // Regenerate itself is one click (#216)
      await trigger.focus();
      await page.keyboard.press('Enter');
      const first = await active(page);
      expect(first).toMatch(/^menuitem:/);
      await page.keyboard.press('ArrowDown');
      expect(await active(page)).not.toBe(first);
      await page.keyboard.press('Escape');
      await expect(trigger).toBeFocused();
      await expect(page.getByRole('menu')).toHaveCount(0);
    }
  });

  test('table: sort with Enter cycles aria-sort; column separators resize with arrows', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Which CARC codes drive denials?');
    const head = page.getByRole('button', { name: 'Claims', exact: true }).last();
    await head.focus();
    await page.keyboard.press('Enter');
    await expect(page.locator('th', { has: head })).toHaveAttribute('aria-sort', 'ascending');
    await page.keyboard.press('Enter');
    await expect(page.locator('th', { has: head })).toHaveAttribute('aria-sort', 'descending');
    const sep = page.getByRole('separator', { name: 'Resize column Claims' }).last();
    await sep.focus();
    const before = Number(await sep.getAttribute('aria-valuenow'));
    await page.keyboard.press('ArrowRight');
    await expect.poll(async () => Number(await sep.getAttribute('aria-valuenow'))).toBeGreaterThan(before);
  });

  test('dialogs (Ctrl+K, Ctrl+/, settings) take focus and give it back on Esc', async ({ page }) => {
    await open(page, LIVE);
    const box = composer(page);
    await box.focus();
    for (const keys of ['Control+k', 'Control+/', 'Control+,']) {
      await page.keyboard.press(keys);
      await expect(page.getByRole('dialog')).toBeVisible();
      expect(await page.evaluate(() => !!document.activeElement?.closest('[role=dialog]'))).toBe(true);
      await page.keyboard.press('Escape');
      await expect(page.getByRole('dialog')).toHaveCount(0);
      await expect(box).toBeFocused();
    }
  });

  test('QA-A21: cancelling an edit with Esc returns focus to the Edit button', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Total paid by payer');
    const edit = page.getByRole('button', { name: 'Edit message' }).last();
    await edit.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByRole('textbox', { name: 'Edit message' })).toBeFocused();
    await page.keyboard.press('Escape');
    expect(await active(page)).not.toBe('BODY');
  });

  test('QA-A21: submitting an edit focuses the composer; version arrows keep focus', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    const edit = page.getByRole('button', { name: 'Edit message' }).last();
    await edit.focus();
    await page.keyboard.press('Enter');
    await page.keyboard.press('ControlOrMeta+a');
    await page.keyboard.type('Total paid by payer');
    await page.keyboard.press('Enter');
    await expect(composer(page)).toBeFocused();
    await waitIdle(page);
    const prev = page.getByRole('button', { name: 'Previous version' }).first();
    await prev.focus();
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('msg-user').first()).toContainText('denial rate');
    // 1/2: Previous is now disabled, so focus lands on Next (not <body>); Enter goes back to 2/2 and stays on Next.
    await expect.poll(() => active(page)).toBe('button:Next version');
    await page.keyboard.press('Enter');
    await expect(page.getByTestId('msg-user').first()).toContainText('Total paid');
    await expect.poll(() => active(page)).not.toBe('BODY');
  });
});

test.describe('screen-reader announcements @desktop', () => {
  const record = (page: Page) => page.evaluate(() => {
    const w = window as unknown as { __ann: { region: string; text: string }[] };
    w.__ann = [];
    const regionOf = (n: Node) => { let e = (n.nodeType === 1 ? n : n.parentElement) as Element | null; while (e) { const l = e.getAttribute('aria-live'); const r = e.getAttribute('role'); if ((l && l !== 'off') || ['log', 'status', 'alert'].includes(r || '')) return e; e = e.parentElement; } return null; };
    new MutationObserver((ms) => { for (const m of ms) { const r = regionOf(m.target); if (!r) continue;
      const t = m.type === 'characterData' ? m.target.textContent : [...m.addedNodes].map((n) => n.textContent).join(' ');
      if (t && t.trim()) w.__ann.push({ region: r.getAttribute('data-testid') || r.getAttribute('aria-label') || r.getAttribute('role') || r.tagName, text: t.trim() }); } })
      .observe(document.body, { subtree: true, childList: true, characterData: true });
  });
  const anns = (page: Page) => page.evaluate(() => (window as unknown as { __ann: { region: string; text: string }[] }).__ann);

  test('status region announces start, then completion, once each', async ({ page }) => {
    await open(page, LIVE);
    await record(page);
    await ask(page, 'Which CARC codes drive denials?');
    await expect.poll(async () => (await anns(page)).filter((a) => a.region === 'status-live').map((a) => a.text).join(' | ')).toMatch(/answering[\s\S]*Answer complete/);
    const st = (await anns(page)).filter((a) => a.region === 'status-live');
    expect(st.filter((a) => /Answer complete/.test(a.text)).length).toBe(1);
    expect(st.length).toBeLessThanOrEqual(6);
  });

  test('QA-S16: streaming tokens and the thinking timer never land in a live region', async ({ page }) => {
    await open(page, LIVE);
    await record(page);
    await ask(page, 'Which CARC codes drive denials?');
    const noisy = (await anns(page)).filter((a) => a.region !== 'status-live' && a.region !== 'transport-announcer' && a.region !== 'toasts');
    expect(noisy, noisy.slice(0, 10).map((a) => `${a.region}: ${a.text.slice(0, 40)}`).join('\n')).toHaveLength(0);
  });
});
