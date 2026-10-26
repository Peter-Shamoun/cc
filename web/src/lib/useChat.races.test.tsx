import { describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useChat } from './useChat';
import { MockAnalystAdapter } from '../adapters/mock';
import { MemoryTranscriptStore } from '../adapters/memoryStore';

describe('useChat races', () => {
  it('a rapid double-send (same tick, before busy renders) starts one turn', async () => {
    const adapter = new MockAnalystAdapter(0);
    const spy = vi.spyOn(adapter, 'streamTurn');
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    act(() => { void result.current.send('Total paid by payer'); void result.current.send('Total paid by payer'); });
    await waitFor(() => expect(result.current.messages.length).toBeGreaterThan(1));
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(spy).toHaveBeenCalledTimes(1);
    expect(result.current.messages.filter((m) => m.role === 'user')).toHaveLength(1);
  });

  it('double-clicked Regenerate starts one re-run', async () => {
    const adapter = new MockAnalystAdapter(0);
    const spy = vi.spyOn(adapter, 'streamTurn');
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    await act(async () => { await result.current.send('Total paid by payer'); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const before = spy.mock.calls.length;
    act(() => { void result.current.regenerate(); void result.current.regenerate(); });
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(before));
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(spy.mock.calls.length - before).toBe(1);
  });

  it('send while an edit is starting is ignored; the edit wins', async () => {
    const adapter = new MockAnalystAdapter(0);
    const spy = vi.spyOn(adapter, 'streamTurn');
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    await act(async () => { await result.current.send('Total paid by payer'); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const before = spy.mock.calls.length;
    act(() => { void result.current.editAt(0, 'Denial rate by payer'); void result.current.send('Something else'); });
    await waitFor(() => expect(spy.mock.calls.length).toBeGreaterThan(before));
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(spy.mock.calls.length - before).toBe(1);
    expect(result.current.messages.filter((m) => m.role === 'user').map((m) => (m as { text: string }).text)).toEqual(['Denial rate by payer']);
  });
});
