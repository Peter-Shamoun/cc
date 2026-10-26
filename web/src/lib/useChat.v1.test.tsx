import { describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { useChat } from './useChat';
import { MockAnalystAdapter } from '../adapters/mock';
import { MemoryTranscriptStore } from '../adapters/memoryStore';
import { HttpAnalystAdapter } from '../adapters/http';
import { V1_FIXTURES } from '../fixtures/v1';
import type { AssistantViewV1 } from '../api/contract';

describe('useChat on contract v1', () => {
  it('reduces v1 steps/escalations and passes clarification resume to the adapter', async () => {
    const adapter = new MockAnalystAdapter(0);
    const spy = vi.spyOn(adapter, 'streamTurn');
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    const prov = V1_FIXTURES.find((f) => f.id === 'v1-providers-repair')!;
    await act(async () => { await result.current.send(prov.prompt); });
    await waitFor(() => expect(result.current.busy).toBe(false));
    const a = result.current.messages[1] as AssistantViewV1;
    expect(a.steps.map((s) => s.kind)).toContain('repair');
    expect(a.escalations[0].to.model).toBe('Sonnet');
    expect(a.usage?.costIsEstimate).toBe(true);
    expect(a.lastSeq).toBeGreaterThan(10);

    await act(async () => { await result.current.send('Last 12 months', { resume: { clarificationId: 'clr_demo_1', choiceIds: ['c2'] } }); });
    expect(spy.mock.calls.at(-1)![0].resume).toEqual({ clarificationId: 'clr_demo_1', choiceIds: ['c2'] });
    // Reopening a chat keeps the work timeline (steps persist with the message).
    const cid = result.current.convId!;
    await act(async () => { await result.current.open(cid); });
    expect((result.current.messages[1] as AssistantViewV1).steps.length).toBeGreaterThan(0);
  });

  it('http adapter sends resume in the POST body only when present', async () => {
    const bodies: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { bodies.push(init.body as string); return new Response('', { status: 500 }); }));
    const h = new HttpAnalystAdapter('');
    const sig = new AbortController().signal;
    for await (const _ of h.streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: 'q' }], signal: sig })) void _;
    for await (const _ of h.streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: 'q' }], signal: sig, resume: { clarificationId: 'x', text: 'Q2' } })) void _;
    expect(JSON.parse(bodies[0]).resume).toBeUndefined();
    expect(JSON.parse(bodies[1]).resume).toEqual({ clarificationId: 'x', text: 'Q2' });
    vi.unstubAllGlobals();
  });
});
