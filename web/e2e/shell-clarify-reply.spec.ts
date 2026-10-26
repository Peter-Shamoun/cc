import { test, expect } from './fixtures';
import { LIVE, open, ask } from './helpers';

// QA-A30: with a clarifying question open, the composer is the reply (Claude/ChatGPT) and resumes that run.
test.describe('composer replies to a clarifying question (live) @desktop', () => {
  test('placeholder is "Reply…" and the send carries ChatRequest.resume', async ({ page }) => {
    await open(page, LIVE);
    await ask(page, 'How bad are our denials?');
    const card = page.getByTestId('msg-assistant').last().getByTestId('clarify');
    await expect(card).toBeVisible();
    const box = page.getByRole('textbox', { name: 'Message' });
    await expect(box).toHaveAttribute('placeholder', 'Reply…');
    const req = page.waitForRequest((r) => r.method() === 'POST' && /\/chat\b/.test(r.url()) && !!r.postData()?.includes('"resume"'));
    await ask(page, 'By billed dollars');
    const body = JSON.parse((await req).postData() ?? '{}');
    expect(body.resume?.clarificationId).toBeTruthy();
    expect(body.resume?.text).toBe('By billed dollars');
    await expect(page.getByTestId('msg-assistant')).toHaveCount(2);
    // The question stays in the thread, marked answered; the composer goes back to follow-ups.
    await expect(page.getByTestId('clarify-question').first()).toBeVisible();
    await expect(box).not.toHaveAttribute('placeholder', 'Reply…');
  });
});
