import { test, expect } from './fixtures';
import { LIVE, open, ask, lastAssistant, composer, stopBtn, waitIdle, sse } from './helpers';

// Coverage for the newest composer, message and table features (QA C7 prep). Live = real demo API.
const chatPosts = (page: import('@playwright/test').Page) => {
  const bodies: Record<string, unknown>[] = [];
  page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/api/chat')) bodies.push(r.postDataJSON()); });
  return bodies;
};
const lastUserText = (b: Record<string, unknown>) => {
  const msgs = b.messages as { role: string; text?: string; content?: string }[];
  const u = msgs.filter((m) => m.role === 'user').pop()!;
  return String(u.text ?? u.content ?? JSON.stringify(u));
};

test.describe('composer features (live) @both', () => {
  test('no data-source picker anywhere: composer is attach / text / send (Peter 11:57 PM)', async ({ page }) => {
    await open(page, LIVE);
    await expect(page.getByTestId('scope-chip')).toHaveCount(0);
    await expect(page.getByText(/Claims lake|sem\.\*/)).toHaveCount(0);
    await page.getByTestId('product-menu').click();
    await expect(page.getByRole('menu')).not.toContainText(/scope|claims lake|sem\./i);
    await page.keyboard.press('Escape');
  });

  test('a long paste becomes a chip, is removable, and rides along with the question', async ({ page, browserName }) => {
    const bodies = chatPosts(page);
    await open(page, LIVE);
    const long = Array.from({ length: 30 }, (_, i) => `row ${i},CO-45,denied`).join('\n');
    // Firefox drops clipboardData from a synthetic ClipboardEvent, so there we do a real copy + paste.
    const paste = browserName === 'firefox'
      ? async (text: string) => {
        await page.evaluate((t) => {
          const ta = document.createElement('textarea'); ta.id = 'qa-clip'; ta.value = t; document.body.appendChild(ta); ta.select();
        }, text);
        await page.keyboard.press('ControlOrMeta+c');
        await page.evaluate(() => document.getElementById('qa-clip')?.remove());
        await composer(page).focus();
        await page.keyboard.press('ControlOrMeta+v');
      }
      : (text: string) => composer(page).evaluate((el, t) => {
        const dt = new DataTransfer();
        dt.setData('text/plain', t);
        el.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
      }, text);
    await composer(page).focus();
    await paste(long);
    const chips = page.getByTestId('paste-chips');
    await expect(chips.getByRole('listitem')).toHaveCount(1);
    await expect(composer(page)).toHaveValue(''); // not dumped into the textarea
    await chips.getByRole('button', { name: /Remove pasted text/ }).click();
    await expect(page.getByTestId('paste-chips')).toHaveCount(0);
    await paste(long);
    await composer(page).fill('Summarize these rows');
    await composer(page).press('Enter');
    await waitIdle(page);
    expect(lastUserText(bodies[bodies.length - 1])).toContain('row 29,CO-45,denied');
    await expect(page.getByTestId('paste-chips')).toHaveCount(0); // cleared after send
  });

  test('attach: a text file becomes a named chip; an oversize file shows a notice', async ({ page }) => {
    const bodies = chatPosts(page);
    await open(page, LIVE);
    const input = page.getByTestId('attach-input');
    await input.setInputFiles({ name: 'carc_codes.csv', mimeType: 'text/csv', buffer: Buffer.from('code,label\nCO-45,Charge exceeds fee schedule\n') });
    await expect(page.getByTestId('paste-chips')).toContainText('carc_codes.csv');
    await input.setInputFiles({ name: 'huge.csv', mimeType: 'text/csv', buffer: Buffer.alloc(300 * 1024, 'a') });
    await expect(page.getByTestId('attach-notice')).toContainText(/huge\.csv.*256 KB/);
    await composer(page).fill('What do these codes mean?');
    await composer(page).press('Enter');
    await waitIdle(page);
    expect(lastUserText(bodies[bodies.length - 1])).toContain('carc_codes.csv:\ncode,label');
  });

  test('Escape stops a live run (server-side cancel)', async ({ page }) => {
    await open(page, LIVE);
    const stops: string[] = [];
    page.on('request', (r) => { if (/\/api\/chat\/[^/]+\/stop$/.test(r.url())) stops.push(r.url()); });
    await composer(page).fill('Run this slowly: denial rate by payer');
    await composer(page).press('Enter');
    await expect(stopBtn(page)).toBeVisible();
    await page.waitForTimeout(400);
    await page.keyboard.press('Escape');
    await waitIdle(page, 5_000);
    await expect(lastAssistant(page).getByTestId('stopped')).toBeVisible();
    expect(stops.length).toBeGreaterThan(0);
  });
});

test.describe('message features (live) @both', () => {
  test('feedback: thumbs-down opens reasons; submit thanks the user and keeps the vote', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    const a = lastAssistant(page);
    await a.getByRole('button', { name: 'Bad response' }).click();
    const panel = a.getByTestId('feedback-panel');
    await expect(panel).toBeVisible();
    await expect(panel.getByRole('button', { name: 'Submit' })).toBeDisabled();
    await panel.locator('button[aria-pressed]').first().click();
    await panel.getByRole('button', { name: 'Submit' }).click();
    await expect(panel).toHaveCount(0);
    await expect(a).toContainText('Thanks for the feedback');
    await expect(a.getByRole('button', { name: 'Bad response' })).toHaveAttribute('aria-pressed', 'true');
    await a.getByRole('button', { name: 'Good response' }).click();
    await expect(a.getByRole('button', { name: 'Good response' })).toHaveAttribute('aria-pressed', 'true');
    await expect(a.getByRole('button', { name: 'Bad response' })).toHaveAttribute('aria-pressed', 'false');
  });

  test('regenerate replaces the last answer and sends regenerate', async ({ page }) => {
    const bodies = chatPosts(page);
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    await lastAssistant(page).getByRole('button', { name: 'Regenerate' }).click();
    await waitIdle(page);
    await expect(page.getByTestId('msg-assistant')).toHaveCount(1);
    await expect(page.getByTestId('msg-user')).toHaveCount(1);
    expect(bodies.length).toBe(2);
    expect(bodies[1].regenerate).toBeTruthy();
    await expect(lastAssistant(page).getByTestId('data-table').first()).toBeVisible();
  });

  test('regenerate keeps the earlier answer as version 1/2 (ChatGPT/Claude parity, QA-A18)', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    await lastAssistant(page).getByRole('button', { name: 'Regenerate' }).click();
    await waitIdle(page);
    await expect(lastAssistant(page)).toContainText(/2\s*\/\s*2/, { timeout: 3000 });
  });

  test('edit a question on live: version 2/2, arrows switch back', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    await page.getByTestId('msg-user').first().hover();
    await page.getByRole('button', { name: 'Edit message' }).first().click();
    await page.getByRole('textbox', { name: 'Edit message' }).fill('Total paid by payer');
    await page.getByRole('button', { name: 'Send', exact: true }).click();
    await waitIdle(page);
    const nav = page.getByTestId('version-nav').first();
    await expect(nav).toContainText(/2\s*\/\s*2/);
    await page.getByRole('button', { name: 'Previous version' }).click();
    await expect(page.getByTestId('msg-user').first()).toContainText('denial rate');
    await expect(nav).toContainText(/1\s*\/\s*2/);
  });
});

test.describe('table column resize (live) @desktop', () => {
  test('drag, arrow keys and double-click reset', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    const table = lastAssistant(page).getByTestId('data-table').first();
    const th = table.locator('th').first();
    const handle = table.getByTestId('col-resize').first();
    await expect(handle).toHaveAttribute('aria-label', /Resize column/);
    const w0 = (await th.boundingBox())!.width;
    const h = (await handle.boundingBox())!;
    await page.mouse.move(h.x + h.width / 2, h.y + h.height / 2);
    await page.mouse.down();
    await page.mouse.move(h.x + h.width / 2 + 80, h.y + h.height / 2, { steps: 8 });
    await page.mouse.up();
    const w1 = (await th.boundingBox())!.width;
    expect(w1).toBeGreaterThan(w0 + 40);
    await handle.focus();
    await page.keyboard.press('ArrowLeft');
    const w2 = (await th.boundingBox())!.width;
    expect(w2).toBeLessThan(w1);
    await handle.dblclick();
    await expect.poll(async () => Math.round((await th.boundingBox())!.width)).toBe(Math.round(w0));
  });
});

test.describe('reconnect after a dropped stream @desktop', () => {
  test('stream drops mid-answer, client re-attaches with Last-Event-ID, text is not duplicated', async ({ page }) => {
    const id = 'm_drop1';
    const all = [
      { type: 'message.start', messageId: id, conversationId: 'c_drop' },
      { type: 'text.start', partId: 't1' },
      { type: 'text.delta', partId: 't1', delta: 'Alpha segment. ' },
      { type: 'text.delta', partId: 't1', delta: 'Omega segment.' },
      { type: 'text.end', partId: 't1' },
      { type: 'message.end', messageId: id, finishReason: 'stop', usage: { queries: 0, elapsedMs: 10 } },
    ];
    const resumes: (string | null)[] = [];
    await page.route('**/api/chat', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: sse(all.slice(0, 3)) }); // then the socket closes
    });
    await page.route(`**/api/chat/${id}/stream`, async (route) => {
      resumes.push(route.request().headers()['last-event-id'] ?? null);
      // Replay only what the client has not seen (ids continue from the drop).
      const rest = all.slice(3).map((e, i) => `id: ${i + 3}\ndata: ${JSON.stringify(e)}\n\n`).join('');
      await route.fulfill({ status: 200, headers: { 'content-type': 'text/event-stream' }, body: rest });
    });
    await open(page, LIVE);
    await ask(page, 'Say something');
    expect(resumes.length).toBeGreaterThan(0);
    expect(resumes[0]).toContain(id);
    const text = await lastAssistant(page).innerText();
    expect(text).toContain('Alpha segment. Omega segment.');
    expect(text.split('Alpha segment.').length - 1).toBe(1);
    await expect(lastAssistant(page).getByTestId('error')).toHaveCount(0);
  });
});
