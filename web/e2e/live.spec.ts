import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import { LIVE, open, ask, lastAssistant, waitIdle, composer, stopBtn } from './helpers';

// Live round trip: browser -> vite proxy -> FastAPI /api/chat SSE -> ScriptedLLM -> synthetic DuckDB.
test.describe('live demo backend @both', () => {
  test('normal answer renders markdown, a result table and a CSV download', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    const a = lastAssistant(page);
    await expect(a.getByTestId('markdown').first()).not.toBeEmpty();
    const table = a.getByTestId('data-table').first();
    await expect(table).toBeVisible();
    await expect(table.getByRole('row').nth(1)).toBeVisible();
    // CSV: full result from the server, not the preview.
    const dl = page.waitForEvent('download');
    await table.hover();
    await page.getByLabel(/download csv/i).first().click();
    const d = await dl;
    expect(d.suggestedFilename()).toMatch(/\.csv$/);
    const csv = fs.readFileSync((await d.path())!, 'utf8');
    expect(csv.split(/\r?\n/)[0]).toMatch(/,/);
    expect(csv.split(/\r?\n/).filter(Boolean).length).toBeGreaterThan(1);
  });

  test('SQL used is available for inspection', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    const a = lastAssistant(page);
    const sql = a.getByTestId('sql-disclosure').or(a.getByTestId('code-block')).first();
    await expect(sql).toBeVisible();
    await sql.getByRole('button').first().click();
    await expect(sql).toContainText(/SELECT[\s\S]+FROM/i);
  });

  test('chart answer renders a plot', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Show the monthly trend of paid amount');
    const chart = lastAssistant(page).getByTestId('chart').first();
    await expect(chart).toBeVisible();
    await expect(chart.locator('svg, canvas').first()).toBeVisible();
  });

  test('tool steps are shown for the run', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Total paid by payer');
    await expect(lastAssistant(page).getByTestId('tool-call').first()).toBeAttached();
  });

  test('refusal for identifiers is a normal answer, not an error', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'List the patient names and member IDs for denied claims');
    const a = lastAssistant(page);
    await expect(a).toContainText(/can.?t|cannot|identif/i);
    await expect(a.getByTestId('error')).toHaveCount(0);
  });

  test('follow-up keeps the thread and pins the new question', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    await ask(page, 'Total paid by payer');
    await expect(page.getByTestId('msg-user')).toHaveCount(2);
    await expect(page.getByTestId('msg-assistant')).toHaveCount(2);
    await expect(page.getByTestId('msg-user').last()).toBeInViewport();
  });

  test('stop cancels a streaming turn and the composer recovers', async ({ page }) => {
    await open(page, LIVE);
    // Hold the SSE open so there is something to stop.
    await page.route('**/api/chat', async (route) => {
      if (route.request().method() !== 'POST') return route.continue();
      await new Promise((r) => setTimeout(r, 15_000));
      await route.abort().catch(() => undefined);
    });
    const stopCalls: string[] = [];
    page.on('request', (r) => { if (/\/api\/chat\/.+\/stop$/.test(r.url())) stopCalls.push(r.url()); });
    await composer(page).fill('What is the denial rate by payer?');
    await composer(page).press('Enter');
    await expect(stopBtn(page)).toBeVisible();
    await stopBtn(page).click();
    await waitIdle(page, 5_000);
    await expect(page.getByTestId('msg-user')).toHaveCount(1);
    await expect(composer(page)).toBeEditable();
  });
});
