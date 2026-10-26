import { test, expect } from './fixtures';
import { LIVE, open, ask, lastAssistant, injectStream, turn, textPart, composer, stopBtn, waitIdle } from './helpers';

/**
 * Every outcome the winning analyzer bundle may produce (Claude's list, 2026-10-25), driven through the
 * live demo backend's contract v1 scenarios (api/CONTRACT.md §9: keywords pick a scripted run on synthetic data).
 */
test.describe('contract v1 outcomes, live demo @both', () => {
  test('answer: text + table + SQL + cost/latency footer', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is the denial rate by payer?');
    const a = lastAssistant(page);
    await expect(a.getByTestId('markdown').first()).not.toBeEmpty();
    await expect(a.getByTestId('data-table').first()).toBeVisible();
    await expect(a.getByTestId('sql-disclosure').or(a.getByTestId('sql-inspector')).or(a.getByTestId('open-sql')).first()).toBeAttached();
    await expect(a.getByTestId('unsupported')).toHaveCount(0);
    const meta = a.getByTestId('answer-meta').or(a.getByTestId('meta-line')).first();
    await expect(meta).toBeAttached();
    await expect(meta).toContainText(/\d(\.\d+)?\s*(ms|s)\b/);
  });

  test('tool steps: SQL running -> repair -> result in the step timeline', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Which CARC codes drive denials?');
    const a = lastAssistant(page);
    const tl = a.getByTestId('step-timeline').first();
    await expect(tl).toBeAttached();
    // Collapsed after the answer, like "Thought for 3s"; expanding shows the repair.
    if (!(await a.getByTestId('repair-error').count())) await a.getByTestId('step-summary').first().click();
    await expect(a.getByTestId('repair-error').or(a.getByTestId('repair-preview')).first()).toBeVisible();
    await expect(a.getByTestId('data-table').first()).toBeVisible();
  });

  test('candidates: multi-candidate comparison is inspectable', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Double check the average paid per claim');
    const a = lastAssistant(page);
    // Timeline collapses after the answer ("Thought for 3s"); expand it, then open the comparison step.
    await a.getByTestId('step-summary').first().click();
    await a.locator('[data-testid="step"][data-kind="candidates"] button').first().click();
    await expect(a.getByTestId('candidates').first()).toBeVisible();
  });

  test('abstain is a first-class outcome, not an error, and suggestions re-ask', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Forecast next quarter denials');
    const a = lastAssistant(page);
    await expect(a.getByTestId('abstain')).toBeVisible();
    await expect(a.getByTestId('error')).toHaveCount(0);
    await expect(a.getByTestId('data-table')).toHaveCount(0);
    const chip = a.getByTestId('rephrase-chip').first();
    if (await chip.count()) {
      const t = ((await chip.textContent()) ?? '').trim();
      await chip.click();
      await expect(page.getByTestId('msg-user')).toHaveCount(2);
      await expect(page.getByTestId('msg-user').last()).toContainText(t.slice(0, 15));
    }
  });

  test('clarify: choices render and picking one resumes with an answer', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'How bad are our denials?');
    const card = lastAssistant(page).getByTestId('clarify');
    await expect(card).toBeVisible();
    const chip = card.getByTestId('clarify-chip').first().or(card.getByRole('button').first()).first();
    const label = ((await chip.textContent()) ?? '').trim();
    await chip.click();
    await waitIdle(page);
    await expect(page.getByTestId('msg-assistant')).toHaveCount(2);
    if (label) await expect(page.getByTestId('msg-user').last()).toContainText(label.slice(0, 12));
    await expect(lastAssistant(page).getByTestId('data-table').first()).toBeVisible();
    // The asking card is now marked answered and can't be clicked again.
    await expect(page.getByTestId('clarify-answered').first()).toBeAttached();
  });

  test('clarify: free text answer also resumes', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'How bad are our denials?');
    await expect(lastAssistant(page).getByTestId('clarify')).toBeVisible();
    await ask(page, 'By billed dollars');
    await expect(page.getByTestId('msg-assistant')).toHaveCount(2);
    await expect(lastAssistant(page).getByTestId('clarify')).toHaveCount(0);
  });

  test('escalation indicator (Haiku -> Sonnet) with both models in usage', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is our net collection rate by payer?');
    const a = lastAssistant(page);
    await expect(a.getByTestId('escalation').first()).toBeVisible();
    await expect(a.getByTestId('escalation').first()).toContainText(/sonnet/i);
  });

  test('cost popover shows estimate, tokens and per-model rows', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'What is our net collection rate by payer?');
    const a = lastAssistant(page);
    const meta = a.getByTestId('meta-line').or(a.getByTestId('answer-meta')).first();
    await meta.hover();
    await meta.click().catch(() => {});
    const pop = page.getByTestId('meta-popover');
    await expect(pop).toBeVisible();
    await expect(pop).toContainText(/\$\d/);
    await expect(pop).toContainText(/haiku/i);
    await expect(pop).toContainText(/sonnet/i);
    await expect(pop).toContainText(/estimate/i);
  });

  test('error scenario shows a retryable error card', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'Simulate an error please');
    const err = lastAssistant(page).getByTestId('error').first();
    await expect(err).toBeVisible();
    await expect(err.getByRole('button', { name: /retry/i })).toBeVisible();
  });

  test('stop on a slow run: server-side cancel, Stopped marker', async ({ page }) => {
    await open(page, LIVE);
    const stops: string[] = [];
    page.on('request', (r) => { if (/\/api\/chat\/[^/]+\/stop$/.test(r.url())) stops.push(r.url()); });
    await composer(page).fill('Run this slowly: denial rate by payer');
    await composer(page).press('Enter');
    await expect(stopBtn(page)).toBeVisible();
    await page.waitForTimeout(400);
    await stopBtn(page).click();
    await waitIdle(page, 5_000);
    await expect(lastAssistant(page).getByTestId('stopped')).toBeVisible();
    expect(stops.length).toBeGreaterThan(0);
  });

  test('unknown future event types degrade gracefully', async ({ page }) => {
    await open(page, LIVE);
    await injectStream(page, turn([
      { type: 'some.future.event', foo: 1 },
      { type: 'step.future_kind', stepId: 's9' },
      { type: 'part', part: { type: 'hologram', id: 'h1' } },
      ...textPart('t1', 'Still here.'),
    ]));
    await ask(page, 'future');
    await expect(lastAssistant(page)).toContainText('Still here.');
  });

  test('HTTP 500 shows a retryable error', async ({ page }) => {
    await open(page, LIVE);
    await page.route('**/api/chat', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 500, body: 'boom' }) : r.continue()));
    await ask(page, 'fail please');
    await expect(lastAssistant(page).getByTestId('error')).toBeVisible();
  });
});
