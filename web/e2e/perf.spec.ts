import { test, expect } from './fixtures';
import { LIVE, open, injectStream, turn, textPart, composer, waitIdle } from './helpers';

/** Performance budgets: 500-message thread, 10k-row table. Numbers go to the QA scorecard. */
test.describe.configure({ mode: 'serial' });

test('500-message thread stays responsive @perf', async ({ page }) => {
  test.setTimeout(600_000);
  await open(page, LIVE);
  const body = 'Denials rose **3.1%** quarter on quarter. Cascade Medicaid drove most of it, mostly CARC 16 and 197.\n\n- Aetna 8.2%\n- BCBS 6.9%\n- Cigna 5.4%';
  await injectStream(page, (n) => turn(textPart(`t${n}`, `${n}: ${body}`)));
  for (let i = 0; i < 250; i++) {
    await composer(page).fill(`Question ${i}: denial rate by payer?`);
    await composer(page).press('Enter');
    await waitIdle(page);
  }
  // Long threads are virtualized (ThreadView, #138): only a window of turns is in the DOM; check the newest turn landed.
  await expect(page.getByTestId('msg-assistant').last()).toContainText(/\b(249|250): Denials/);
  const domNodes = await page.evaluate(() => document.querySelectorAll('*').length);
  // Typing latency: time from keypress to value update, averaged.
  const t0 = Date.now();
  await composer(page).pressSequentially('typing latency probe', { delay: 0 });
  const typeMs = (Date.now() - t0) / 'typing latency probe'.length;
  // Scroll jank: long frames while scrolling to top.
  const long = await page.evaluate(async () => {
    const el = document.querySelector('main .overflow-y-auto') as HTMLElement;
    let worst = 0; let last = performance.now(); let frames = 0;
    await new Promise<void>((res) => {
      const step = () => {
        const now = performance.now(); worst = Math.max(worst, now - last); last = now; frames++;
        el.scrollTop = Math.max(0, el.scrollTop - 400);
        if (el.scrollTop > 0 && frames < 600) requestAnimationFrame(step); else res();
      };
      requestAnimationFrame(step);
    });
    return { worst, frames };
  });
  const sendT = Date.now();
  await composer(page).fill('one more');
  await composer(page).press('Enter');
  await waitIdle(page);
  const turnMs = Date.now() - sendT;
  test.info().annotations.push({ type: 'perf', description: JSON.stringify({ messages: 500, domNodes, typeMs, worstFrameMs: long.worst, turnMs }) });
  expect(typeMs).toBeLessThan(50);
  expect(long.worst).toBeLessThan(200);
});

test('10k-row table renders and scrolls @perf', async ({ page }) => {
  await open(page, LIVE);
  const columns = [
    { key: 'claim', label: 'Claim', type: 'code' }, { key: 'payer', label: 'Payer', type: 'string' },
    { key: 'billed', label: 'Billed', type: 'currency' }, { key: 'paid', label: 'Paid', type: 'currency' },
    { key: 'rate', label: 'Denial rate', type: 'percent' }, { key: 'dos', label: 'Service date', type: 'date' },
  ];
  const payers = ['Aetna', 'BCBS', 'Cigna', 'Humana', 'Medicare', 'Medicaid'];
  const rows = Array.from({ length: 10_000 }, (_, i) => [`CLM-${100000 + i}`, payers[i % 6], 100 + (i % 977), 50 + (i % 433), (i % 100) / 1000, `2026-0${1 + (i % 9)}-1${i % 9}`]);
  await injectStream(page, turn([...textPart('t1', 'Here are all claims.'), { type: 'part', part: { type: 'table', id: 'tb1', title: 'All claims', columns, rows, rowCount: 10_000 } }]));
  const t0 = Date.now();
  await composer(page).fill('all claims');
  await composer(page).press('Enter');
  const table = page.getByTestId('data-table').first();
  await expect(table).toBeVisible();
  const renderMs = Date.now() - t0;
  const domRows = await table.getByRole('row').count();
  test.info().annotations.push({ type: 'perf', description: JSON.stringify({ rows: 10_000, renderMs, domRows }) });
  expect(renderMs).toBeLessThan(3000);
  expect(domRows).toBeLessThan(500); // virtualised or paginated
});
