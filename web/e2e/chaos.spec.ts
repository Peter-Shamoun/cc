import { test, expect } from './fixtures';
import type { Page } from '@playwright/test';
import { LIVE, open, ask, composer, sendBtn, stopBtn, lastAssistant, waitIdle, sse, contractExample, gap, type Ev } from './helpers';

// Chaos pass (PETER DIRECTIVE: every edge case). Faults at every stream event, malformed and odd streams, HTTP failures,
// reload at every stage, multi-tab, 200% zoom / 320px reflow, RTL, and a seeded monkey. Desktop project only (@chaos is
// excluded from the default runs; use `npm run e2e:chaos`).

const crashWatch = (page: Page) => {
  const errs: string[] = [];
  page.on('pageerror', (e) => errs.push(String(e.message).slice(0, 200)));
  return errs;
};
const noBoundary = async (page: Page) => expect(page.getByText(/unexpected error|Something broke/i)).toHaveCount(0);
const noHScroll = async (page: Page) => {
  const { sw, cw } = await page.evaluate(() => ({ sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth }));
  expect(sw, 'document scrolls sideways').toBeLessThanOrEqual(cw + 1);
};
const fulfillSse = (body: string) => ({ status: 200, headers: { 'content-type': 'text/event-stream', 'cache-control': 'no-store' }, body });

test.describe('chaos: stream faults @chaos', () => {
  const full = contractExample('report') ?? contractExample('answer')!;

  test('cut the stream after every event index: always ends idle, shows content or a retryable error, Retry recovers', async ({ page }) => {
    test.setTimeout(240_000);
    const errs = crashWatch(page);
    let cut = 0;
    let nextFull = false;
    await page.route('**/api/chat', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      if (nextFull) { nextFull = false; return route.fulfill(fulfillSse(sse(full))); }
      if (cut === 0) return route.abort('connectionreset');
      return route.fulfill(fulfillSse(sse(full.slice(0, cut))));
    });
    await open(page, LIVE);
    const bad: string[] = [];
    for (cut = 0; cut < full.length; cut++) {
      await ask(page, `chaos cut ${cut}`);
      const a = lastAssistant(page);
      const txt = (await a.innerText()).trim();
      const ended = full.slice(0, cut).some((e) => e.type === 'message.end');
      const hasRetry = await a.getByRole('button', { name: /^retry/i }).count();
      if (!ended && !hasRetry) bad.push(`cut ${cut} (${full[cut - 1]?.type ?? 'pre-request'}): no Retry; text="${txt.slice(0, 60)}"`);
      if (cut % 7 === 3 && hasRetry) {
        nextFull = true;
        await a.getByRole('button', { name: /^retry/i }).first().click();
        await waitIdle(page);
        await expect(lastAssistant(page).getByRole('button', { name: /^retry/i })).toHaveCount(0);
      }
      await expect(composer(page)).toBeEditable();
    }
    await noBoundary(page);
    expect(errs, 'uncaught page errors').toEqual([]);
    expect(bad, 'cuts without a recovery path').toEqual([]);
  });

  test('malformed / odd streams never crash the page', async ({ page }) => {
    const errs = crashWatch(page);
    const end = (id: string) => ({ type: 'message.end', messageId: id, finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 } });
    const st = (id: string) => ({ type: 'message.start', messageId: id, conversationId: 'c_chaos' });
    const bodies: string[] = [
      'data: {not json}\n\n' + sse([st('m1'), { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: 'ok after junk' }, { type: 'text.end', partId: 't' }, end('m1')]),
      sse([st('m2'), { type: 'text.delta', partId: 'ghost', delta: 'delta for unknown part' }, end('m2')]),
      sse([st('m3'), { type: 'future.event', foo: 1 } as Ev, { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: 'unknown event skipped' }, { type: 'text.end', partId: 't' }, end('m3')]),
      sse([st('m4'), { type: 'text.start', partId: 't' }, { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: 'dup start' }, { type: 'text.end', partId: 't' }, { type: 'text.end', partId: 't' }, end('m4'), end('m4')]),
      sse([st('m5'), { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: 'x'.repeat(200_000) }, { type: 'text.end', partId: 't' }, end('m5')]),
      sse([st('m6'), { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: '<img src=x onerror=alert(1)><script>alert(2)</script> **md** [js](javascript:alert(3))' }, { type: 'text.end', partId: 't' }, end('m6')]),
      sse([end('m7')]),
      '',
      ': keep-alive\n\n'.repeat(50) + sse([st('m9'), { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: 'after heartbeats' }, { type: 'text.end', partId: 't' }, end('m9')]),
      sse([st('m10'), { type: 'text.start', partId: 't' }, { type: 'text.delta', partId: 't', delta: '😀\u200f مرحبا 𝔘𝔫𝔦𝔠𝔬𝔡𝔢 \u0000 nul' }, { type: 'text.end', partId: 't' }, end('m10')]),
    ];
    let i = 0;
    let dialogs = 0;
    page.on('dialog', (d) => { dialogs++; void d.dismiss(); });
    await page.route('**/api/chat', (route) => route.request().method() === 'POST' ? route.fulfill(fulfillSse(bodies[i++] ?? '')) : route.continue());
    await open(page, LIVE);
    for (let k = 0; k < bodies.length; k++) {
      await composer(page).fill(`malformed ${k}`);
      await composer(page).press('Enter');
      await expect(stopBtn(page)).toHaveCount(0, { timeout: 20_000 });
      await expect(composer(page)).toBeEditable();
    }
    await expect(page.getByText('ok after junk')).toBeVisible();
    await expect(page.getByText('unknown event skipped')).toBeVisible();
    await expect(page.getByText('after heartbeats')).toBeVisible();
    expect(dialogs, 'no script ran from streamed markdown').toBe(0);
    await expect(page.locator('a[href^="javascript:"]')).toHaveCount(0);
    await noBoundary(page);
    expect(errs).toEqual([]);
  });

  for (const [label, status, body] of [
    ['500 html', 500, '<html>oops</html>'], ['502 empty', 502, ''], ['503 json', 503, '{"error":{"code":"unavailable","message":"down"}}'],
    ['401', 401, '{"error":{"code":"unauthorized","message":"no"}}'], ['413', 413, '{"error":{"code":"too_large","message":"big"}}'],
    ['200 json not sse', 200, '{"hello":1}'],
  ] as const) {
    test(`HTTP fault ${label}: readable error, composer keeps the question, no crash`, async ({ page }) => {
      const errs = crashWatch(page);
      await page.route('**/api/chat', (route) => route.request().method() === 'POST'
        ? route.fulfill({ status, headers: { 'content-type': label.includes('html') ? 'text/html' : 'application/json' }, body })
        : route.continue());
      await open(page, LIVE);
      await composer(page).fill('http fault question');
      await composer(page).press('Enter');
      await expect(stopBtn(page)).toHaveCount(0, { timeout: 20_000 });
      const a = lastAssistant(page);
      await expect(a).toBeVisible();
      await expect(a).not.toHaveText(/undefined|\[object Object\]|<html>/);
      await expect(a.getByRole('button', { name: /^retry/i }).or(page.getByRole('alert'))).toHaveCount(1, { timeout: 5_000 }).catch(async () => {
        expect(await a.innerText(), `${label}: no retry/alert`).toMatch(/.+/);
      });
      await noBoundary(page);
      expect(errs).toEqual([]);
    });
  }

  test('server never answers: Stop ends the turn cleanly and the next send works', async ({ page }) => {
    let hang = true;
    await page.route('**/api/chat', async (route) => {
      if (route.request().method() !== 'POST' || !hang) return route.continue();
      hang = false; // leave this request hanging forever
    });
    await open(page, LIVE);
    await composer(page).fill('hang please');
    await composer(page).press('Enter');
    await expect(stopBtn(page)).toBeVisible();
    await page.waitForTimeout(1500);
    await stopBtn(page).click();
    await expect(stopBtn(page)).toHaveCount(0, { timeout: 5_000 });
    await ask(page, 'What was our denial rate by payer last quarter?');
    await expect(lastAssistant(page)).toContainText(/denial/i);
  });

  test('double/triple Enter and rapid Stop/Send mashing never duplicate a turn', async ({ page }) => {
    const errs = crashWatch(page);
    let posts = 0;
    page.on('request', (r) => { if (r.method() === 'POST' && r.url().endsWith('/api/chat')) posts++; });
    await open(page, LIVE);
    await composer(page).fill('What was our denial rate by payer last quarter?');
    await composer(page).press('Enter');
    await composer(page).press('Enter');
    await composer(page).press('Enter');
    await waitIdle(page);
    expect(posts).toBe(1);
    await expect(page.getByTestId('msg-user')).toHaveCount(1);
    for (let k = 0; k < 6; k++) {
      await composer(page).fill(`mash ${k}`);
      await composer(page).press('Enter');
      if (await stopBtn(page).count()) await stopBtn(page).click({ timeout: 2_000 }).catch(() => {});
    }
    await waitIdle(page);
    const users = await page.getByTestId('msg-user').count();
    const asst = await page.getByTestId('msg-assistant').count();
    expect(asst).toBe(users);
    await noBoundary(page);
    expect(errs).toEqual([]);
  });
});

test.describe('chaos: reload at every stage @chaos', () => {
  const stages: [string, (p: Page) => Promise<void>][] = [
    ['empty with typed draft', async (p) => { await composer(p).fill('half-typed'); }],
    ['right after send', async (p) => { await composer(p).fill('What was our denial rate by payer last quarter?'); await composer(p).press('Enter'); }],
    ['mid stream', async (p) => { await composer(p).fill('What was our denial rate by payer last quarter?'); await composer(p).press('Enter'); await expect(stopBtn(p)).toBeVisible(); await p.waitForTimeout(400); }],
    ['after answer', async (p) => { await ask(p, 'What was our denial rate by payer last quarter?'); }],
    ['clarify pending', async (p) => { await ask(p, 'Show me the trend'); }],
    ['edit open', async (p) => { await ask(p, 'What was our denial rate by payer last quarter?'); await p.getByTestId('msg-user').last().hover(); await p.getByRole('button', { name: /^edit/i }).last().click(); }],
    ['settings open', async (p) => { await ask(p, 'What was our denial rate by payer last quarter?'); await p.keyboard.press('Control+,'); }],
  ];
  for (const [name, act] of stages) {
    test(`reload: ${name}`, async ({ page }) => {
      const errs = crashWatch(page);
      await open(page, LIVE);
      await act(page);
      const urlBefore = page.url();
      await page.reload();
      await expect(composer(page)).toBeVisible();
      await expect(composer(page)).toBeEditable();
      await noBoundary(page);
      // A chat that had started is still reachable at its URL, and it isn't left spinning forever.
      if (/\/c\//.test(urlBefore)) {
        expect(page.url()).toBe(urlBefore);
        await expect(stopBtn(page)).toHaveCount(0, { timeout: 30_000 });
        await expect(page.getByTestId('msg-user').first()).toBeVisible();
      }
      // Still usable.
      await ask(page, 'What was our denial rate by payer last quarter?');
      await expect(lastAssistant(page)).toContainText(/denial/i);
      expect(errs).toEqual([]);
    });
  }
});

test.describe('chaos: multi-tab @chaos', () => {
  test('a chat created in tab A appears in tab B when B regains focus', async ({ context }) => {
    gap('QA-S21');
    const a = await context.newPage();
    const b = await context.newPage();
    await open(a, LIVE); await open(b, LIVE);
    await ask(a, 'Multi tab sentinel: denial rate by payer');
    const title = (await a.title()).replace(/ · .*$| - .*$/, '');
    await b.bringToFront();
    await b.evaluate(() => { document.dispatchEvent(new Event('visibilitychange')); window.dispatchEvent(new Event('focus')); });
    await expect(b.getByRole('navigation').getByText(/Multi tab sentinel|denial rate/i).first(), `sidebar in B should list "${title}"`).toBeVisible({ timeout: 8_000 });
  });

  test('a chat deleted in tab A fails gracefully in tab B', async ({ context }) => {
    const errs: string[] = [];
    const a = await context.newPage();
    await open(a, LIVE);
    await ask(a, 'What was our denial rate by payer last quarter?');
    const url = a.url();
    const b = await context.newPage();
    b.on('pageerror', (e) => errs.push(e.message));
    await b.goto(url);
    await expect(b.getByTestId('msg-user').first()).toBeVisible();
    const id = url.split('/').pop()!;
    const del = await a.request.delete(`${new URL(url).origin}/api/conversations/${id}`);
    expect(del.ok()).toBeTruthy();
    await b.bringToFront();
    await ask(b, 'follow-up after delete', { wait: false });
    await expect(stopBtn(b)).toHaveCount(0, { timeout: 20_000 });
    await expect(composer(b)).toBeEditable();
    await expect(b.getByText(/unexpected error/i)).toHaveCount(0);
    expect(errs).toEqual([]);
  });
});

test.describe('chaos: zoom, reflow, RTL @chaos', () => {
  for (const [label, vp] of [['200% zoom of 1440x900', { width: 720, height: 450 }], ['WCAG 1.4.10 reflow 320px', { width: 320, height: 640 }], ['400% zoom of 1280', { width: 320, height: 256 }]] as const) {
    test(`reflow: ${label}`, async ({ page }) => {
      await page.setViewportSize(vp);
      await open(page, LIVE);
      await noHScroll(page);
      await ask(page, 'What was our denial rate by payer last quarter?');
      await noHScroll(page);
      await expect(composer(page)).toBeInViewport();
      await expect(sendBtn(page)).toBeInViewport();
      await page.screenshot({ path: test.info().outputPath(`reflow-${vp.width}x${vp.height}.png`) });
    });
  }

  test('RTL: dir=rtl document and Arabic/Hebrew content keep the layout intact', async ({ page }) => {
    await open(page, LIVE);
    await page.evaluate(() => { document.documentElement.dir = 'rtl'; document.documentElement.lang = 'ar'; });
    await noHScroll(page);
    await ask(page, 'ما هو معدل الرفض حسب الدافع؟ What was our denial rate by payer last quarter?');
    await noHScroll(page);
    const dir = await page.getByTestId('msg-user').last().evaluate((el) => getComputedStyle(el.querySelector('[dir]') ?? el).direction);
    expect(dir).toBe('rtl');
    await expect(composer(page)).toBeInViewport();
    await page.screenshot({ path: test.info().outputPath('rtl.png'), fullPage: false });
  });
});

test.describe('chaos: monkey @chaos', () => {
  for (const seed of [7, 42, 1337]) {
    test(`monkey seed ${seed}: 120 random actions, no crash, still answers`, async ({ page }) => {
      test.setTimeout(180_000);
      const errs = crashWatch(page);
      page.on('dialog', (d) => void d.dismiss());
      page.on('popup', (p) => void p.close());
      await page.addInitScript(() => { window.print = () => {}; });
      let s = seed;
      const rnd = () => { s = (s * 1103515245 + 12345) % 2147483648; return s / 2147483648; };
      await open(page, LIVE);
      await ask(page, 'What was our denial rate by payer last quarter?');
      const prompts = ['Show me the trend', 'Top denial reasons as a chart', 'hi', ''];
      const log: string[] = [];
      for (let k = 0; k < 120; k++) {
        const r = rnd();
        try {
          if (r < 0.08) { const pr = prompts[Math.floor(rnd() * prompts.length)]; await composer(page).fill(pr, { timeout: 1000 }); await composer(page).press('Enter'); log.push(`send:${pr}`); }
          else if (r < 0.16) { await page.keyboard.press(['Escape', 'Tab', 'Enter', 'ArrowDown', 'Control+k', 'Control+/', 'Control+Shift+S', 'Space'][Math.floor(rnd() * 8)]); log.push('key'); }
          else if (r < 0.2) { await page.mouse.wheel(0, (rnd() - 0.5) * 3000); log.push('wheel'); }
          else if (r < 0.23) { await page.setViewportSize([{ width: 1440, height: 900 }, { width: 390, height: 844 }, { width: 900, height: 700 }][Math.floor(rnd() * 3)]); log.push('resize'); }
          else {
            const els = page.locator('button:visible, [role="menuitem"]:visible, [role="tab"]:visible, [role="radio"]:visible, summary:visible, a[href^="/"]:visible');
            const n = await els.count();
            if (!n) continue;
            const el = els.nth(Math.floor(rnd() * n));
            const name = ((await el.getAttribute('aria-label').catch(() => null)) ?? (await el.innerText({ timeout: 500 }).catch(() => '')) ?? '').slice(0, 30);
            if (/print|download|sign out/i.test(name)) continue;
            await el.click({ timeout: 1000, trial: false });
            log.push(`click:${name}`);
          }
        } catch { log.push('miss'); }
        if (errs.length) break;
      }
      expect(errs, `page errors after: ${log.slice(-8).join(' | ')}`).toEqual([]);
      await noBoundary(page);
      await page.keyboard.press('Escape');
      await page.keyboard.press('Escape');
      await page.setViewportSize({ width: 1440, height: 900 });
      await page.goto(LIVE);
      await ask(page, 'What was our denial rate by payer last quarter?');
      await expect(lastAssistant(page)).toContainText(/denial/i);
    });
  }
});
