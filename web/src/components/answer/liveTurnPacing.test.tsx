/** Streaming pacing at the turn level: a whole answer that lands in one network chunk still flows in (ChatGPT/Claude). */
import { afterEach, describe, expect, it } from 'vitest';
import { cleanup, render, screen, waitFor } from '@testing-library/react';
import { AssistantTurn } from '../Message';
import { newAssistantViewV1, reduceV1, type AssistantViewV1, type StreamEvent } from '../../api/contract';

afterEach(cleanup);
const para = 'Cigna has the highest denial rate at 20.4%, followed by Medicaid at 20.3%. '.repeat(8).trim();
const done = (id: string) => [
  { type: 'message.start', messageId: id, conversationId: 'c1' },
  { type: 'text.start', partId: 't1' },
  { type: 'text.delta', partId: 't1', delta: para },
  { type: 'text.end', partId: 't1' },
  { type: 'message.end', messageId: id, finishReason: 'stop', usage: { queries: 1, elapsedMs: 10 } },
].reduce((m, e) => reduceV1(m, e as StreamEvent), newAssistantViewV1(id) as AssistantViewV1);
const noop = () => {};
const md = () => screen.findByTestId('markdown');

describe('turn-level pacing', () => {
  it('a turn that was streaming on screen paces text that arrived already closed (one-chunk stream)', async () => {
    const placeholder = { ...newAssistantViewV1('ph'), uiKey: 'ph' };
    const { rerender } = render(<AssistantTurn msg={placeholder} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    rerender(<AssistantTurn msg={{ ...done('a1'), uiKey: 'ph' } as AssistantViewV1} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    const el = await md();
    expect(el.textContent!.length).toBeLessThan(para.length);
    await waitFor(() => expect(el.textContent).toBe(para), { timeout: 3000 });
  });
  it('history (never streamed here) shows at once', async () => {
    render(<AssistantTurn msg={done('a2')} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect((await md()).textContent).toBe(para);
  });
  it('switching to another version in the same row shows it at once (no replay)', async () => {
    const placeholder = { ...newAssistantViewV1('ph'), uiKey: 'ph' };
    const { rerender } = render(<AssistantTurn msg={placeholder} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    rerender(<AssistantTurn msg={{ ...done('old'), uiKey: 'old' } as AssistantViewV1} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
    expect((await md()).textContent).toBe(para);
  });
});
