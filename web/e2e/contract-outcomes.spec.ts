import { test, expect } from '@playwright/test';
import { LIVE, open, ask, lastAssistant, injectStream, contractExample, turn, textPart } from './helpers';

/**
 * Every outcome the winning analyzer bundle may produce (Claude's list, 2026-10-25):
 * answer+table+SQL, streaming tool steps, abstain, clarify, escalation, cost/latency.
 * Streams come from api/contract/examples/<outcome>.jsonl (Contract team) and are replayed
 * through the live app's HTTP adapter. Required test ids (see /workspace/cc_fe/NOTES.md):
 *   abstain, clarify (+ buttons for options), escalation, answer-meta.
 */
const need = (outcome: string) => {
  const ev = contractExample(outcome);
  test.skip(!ev, `pending: api/contract/examples/${outcome}.jsonl not published yet`);
  return ev!;
};

test.describe('contract outcomes @both', () => {
  test('answer: final answer + table + SQL', async ({ page }) => {
    const ev = need('answer');
    await open(page, LIVE);
    await injectStream(page, ev);
    await ask(page, 'contract answer');
    const a = lastAssistant(page);
    await expect(a.getByTestId('markdown').first()).not.toBeEmpty();
    await expect(a.getByTestId('data-table').first()).toBeVisible();
    await expect(a.getByTestId('sql-disclosure').or(a.getByTestId('code-block')).first()).toBeAttached();
    await expect(a.getByTestId('unsupported')).toHaveCount(0);
  });

  test('tool steps: SQL running, repair, checks', async ({ page }) => {
    const ev = need('tool_steps');
    await open(page, LIVE);
    await injectStream(page, ev);
    await ask(page, 'contract tool steps');
    const a = lastAssistant(page);
    await expect(a.getByTestId('tool-call').first()).toBeAttached();
    await expect(a.getByTestId('unsupported')).toHaveCount(0);
  });

  test('abstain is a first-class outcome, not an error', async ({ page }) => {
    const ev = need('abstain');
    await open(page, LIVE);
    await injectStream(page, ev);
    await ask(page, 'contract abstain');
    const a = lastAssistant(page);
    await expect(a.getByTestId('abstain')).toBeVisible();
    await expect(a.getByTestId('error')).toHaveCount(0);
    await expect(a.getByTestId('unsupported')).toHaveCount(0);
  });

  test('clarify: options render and picking one resumes the conversation', async ({ page }) => {
    const ev = need('clarify');
    await open(page, LIVE);
    await injectStream(page, (n) => (n === 0 ? ev : turn(textPart('t1', 'Thanks, using that.'))));
    await ask(page, 'contract clarify');
    const card = lastAssistant(page).getByTestId('clarify');
    await expect(card).toBeVisible();
    const opt = card.getByRole('button').first();
    const label = (await opt.textContent())?.trim() ?? '';
    await opt.click();
    await expect(page.getByTestId('msg-user')).toHaveCount(2);
    if (label) await expect(page.getByTestId('msg-user').last()).toContainText(label.slice(0, 20));
  });

  test('escalation indicator (Haiku -> Sonnet)', async ({ page }) => {
    const ev = need('escalation');
    await open(page, LIVE);
    await injectStream(page, ev);
    await ask(page, 'contract escalation');
    await expect(lastAssistant(page).getByTestId('escalation')).toBeVisible();
  });

  test('cost and latency per answer', async ({ page }) => {
    const ev = need('answer');
    await open(page, LIVE);
    await injectStream(page, ev);
    await ask(page, 'contract meta');
    const a = lastAssistant(page);
    await a.hover();
    await expect(a.getByTestId('answer-meta')).toBeAttached();
    await expect(a.getByTestId('answer-meta')).toContainText(/\d+(\.\d+)?\s*(ms|s)\b|\$\d/);
  });

  test('unknown future event types degrade gracefully', async ({ page }) => {
    await open(page, LIVE);
    await injectStream(page, turn([
      { type: 'some.future.event', foo: 1 },
      { type: 'part', part: { type: 'hologram', id: 'h1' } },
      ...textPart('t1', 'Still here.'),
    ]));
    await ask(page, 'future');
    const a = lastAssistant(page);
    await expect(a).toContainText('Still here.');
  });

  test('error stream (HTTP 500) shows a retryable error', async ({ page }) => {
    await open(page, LIVE);
    await page.route('**/api/chat', (r) => (r.request().method() === 'POST' ? r.fulfill({ status: 500, body: 'boom' }) : r.continue()));
    await ask(page, 'fail please');
    await expect(lastAssistant(page).getByTestId('error')).toBeVisible();
  });
});
