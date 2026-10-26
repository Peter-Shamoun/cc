import { describe, expect, it } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useChat, withInterrupted, type ViewMessage } from './useChat';
import { MockAnalystAdapter } from '../adapters/mock';
import { MemoryTranscriptStore } from '../adapters/memoryStore';

// Reload mid-answer: the server keeps the question, cancels the run after its disconnect grace (CONTRACT.md §8),
// so the reopened chat ends on the question. It must say so and offer Retry, not hang with nothing under it.
describe('a chat reopened after an interrupted answer', () => {
  it('ends with a retryable "Response interrupted" turn; Retry answers once and leaves no stub version', async () => {
    const adapter = new MockAnalystAdapter(0);
    const store = new MemoryTranscriptStore();
    await store.appendMessage('c1', { id: 'u1', role: 'user', text: 'Total paid by payer', createdAt: new Date(0).toISOString() } as never, 'Total paid by payer');
    const { result } = renderHook(() => useChat(adapter, store));
    await act(async () => { await result.current.open('c1'); });
    const last = result.current.messages.at(-1) as ViewMessage & { parts?: { type: string; title?: string; retryable?: boolean }[]; interrupted?: boolean };
    expect(result.current.messages).toHaveLength(2);
    expect(last.role).toBe('assistant');
    expect(last.interrupted).toBe(true);
    expect(last.parts?.[0]).toMatchObject({ type: 'error', title: 'Response interrupted', retryable: true });
    act(() => { void result.current.regenerate(); });
    await waitFor(() => expect(result.current.busy).toBe(true));
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(result.current.messages.filter((m) => m.role === 'user')).toHaveLength(1);
    const answer = result.current.messages.at(-1) as ViewMessage & { interrupted?: boolean };
    expect(answer.role).toBe('assistant');
    expect(answer.interrupted).toBeFalsy();
    expect(result.current.versionOf(0)).toBeUndefined(); // the stub never became "‹ 1/2 ›"
  });
  it('leaves answered chats and empty chats alone', () => {
    expect(withInterrupted([])).toEqual([]);
    const done = [{ id: 'u', role: 'user', text: 'q' }, { id: 'a', role: 'assistant', parts: [] }] as unknown as ViewMessage[];
    expect(withInterrupted(done)).toBe(done);
  });
});
