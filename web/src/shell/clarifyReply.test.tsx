import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import App from '../App';
import { MockAnalystAdapter } from '../adapters/mock';
import { openClarification } from '../lib/openClarification';
import type { ViewMessage } from '../lib/useChat';

vi.mock('../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

afterEach(() => { cleanup(); vi.restoreAllMocks(); window.history.replaceState(null, '', '/'); });

const box = () => screen.getByRole('textbox', { name: 'Message' }) as HTMLTextAreaElement;
async function type(text: string) {
  fireEvent.change(box(), { target: { value: text } });
  fireEvent.keyDown(box(), { key: 'Enter' });
  await waitFor(() => expect(screen.queryByRole('button', { name: 'Stop generating' })).toBeNull());
}

describe('composer answers an open clarifying question (QA-A30)', () => {
  it('shows "Reply…", sends the text as the resume answer, and keeps the question in the thread', async () => {
    const turns: { resume?: unknown }[] = [];
    const real = MockAnalystAdapter.prototype.streamTurn;
    vi.spyOn(MockAnalystAdapter.prototype, 'streamTurn').mockImplementation(function (this: MockAnalystAdapter, input) {
      turns.push({ resume: input.resume });
      return real.call(this, input);
    });
    render(<App />);
    await type('Show me the denial rate trend');
    await screen.findByTestId('clarify');
    await waitFor(() => expect(box().placeholder).toBe('Reply…'));
    await type('Last 12 months');
    await waitFor(() => expect(turns).toHaveLength(2));
    await waitFor(() => expect(screen.getAllByTestId('msg-assistant')).toHaveLength(2));
    expect(turns[0].resume).toBeUndefined();
    expect(turns[1].resume).toEqual({ clarificationId: 'clr_demo_1', text: 'Last 12 months' });
    // The question stays visible above the reply, now marked answered; the composer is back to a plain follow-up.
    expect(screen.getByTestId('clarify-question').textContent).toContain('Which period');
    await waitFor(() => expect(screen.getByTestId('clarify-answered')).toBeTruthy());
    await waitFor(() => expect(box().placeholder).not.toBe('Reply…'));
    await type('And by payer');
    await waitFor(() => expect(turns).toHaveLength(3));
    expect(turns[2].resume).toBeUndefined();
  });

  it('is only open on the last finished, unanswered answer', () => {
    const clar = { type: 'clarification', id: 'p', clarificationId: 'x', question: 'Which period?', allowFreeText: true };
    const a = (extra: object = {}, parts: object[] = [clar]) => ({ id: 'a', role: 'assistant', parts, steps: [], escalations: [], lastSeq: -1, streaming: false, status: null, openText: [], createdAt: '', ...extra }) as unknown as ViewMessage;
    const u = { id: 'u', role: 'user', text: 'q', createdAt: '' } as ViewMessage;
    expect(openClarification([u, a()])?.clarificationId).toBe('x');
    expect(openClarification([u, a({ streaming: true })])).toBeNull();
    expect(openClarification([u, a({ interrupted: true })])).toBeNull();
    expect(openClarification([u, a({}, [{ ...clar, answered: true }])])).toBeNull();
    expect(openClarification([u, a(), u])).toBeNull();
    expect(openClarification([])).toBeNull();
  });
});
