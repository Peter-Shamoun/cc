import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { HttpAnalystAdapter } from './http';
import { MemoryTranscriptStore } from './memoryStore';
import { useChat } from '../lib/useChat';
import type { AuthSession } from '../auth/session';
import type { AssistantMessage } from '../lib/contract';

// Edge cases while re-attaching to an answer that already started (Last-Event-ID replay).
afterEach(() => { vi.unstubAllGlobals(); });
const enc = new TextEncoder();
const sse = (evs: object[], close = true) => new Response(new ReadableStream({ start(c) { for (const e of evs) c.enqueue(enc.encode(`data: ${JSON.stringify(e)}\n\n`)); if (close) c.close(); } }), { status: 200 });
const start = { type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.13.0', seq: 0 };
const text = { type: 'part', part: { type: 'text', id: 't1', text: 'Denials rose 4% in Q3.' }, seq: 1 };
const end = { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 2 };
const json = (status: number, code: string, headers: Record<string, string> = {}) => new Response(JSON.stringify({ error: { code, message: '' } }), { status, headers });
const session = () => { const onExpired = vi.fn(); return { s: { kind: 'cookie', headers: () => ({}), onExpired } as unknown as AuthSession, onExpired }; };
const drain = async (a: HttpAnalystAdapter, onConnection?: (s: string) => void) => {
  const out: { type: string; part?: { code?: string }; finishReason?: string; messageId?: string }[] = [];
  for await (const e of a.streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: 'q' }], signal: new AbortController().signal, onConnection: onConnection as never })) out.push(e as never);
  return out;
};

describe('http adapter: auth expiry and rate limits mid-stream', () => {
  it('401 on re-attach ends the answer with the sign-in card (not "Connection lost") and calls onExpired once', async () => {
    const res = [sse([start, text]), json(401, 'auth_expired')];
    vi.stubGlobal('fetch', vi.fn(async () => res.shift()!));
    const { s, onExpired } = session();
    const out = await drain(new HttpAnalystAdapter('', s, { retryBaseMs: 1 }));
    expect(out.map((e) => e.type)).toEqual(['message.start', 'part', 'part', 'message.end']);
    expect(out[2].part).toMatchObject({ code: 'auth_expired' });
    expect(out[3]).toMatchObject({ messageId: 'm1', finishReason: 'error' });
    expect(onExpired).toHaveBeenCalledTimes(1);
  });

  it('429 on re-attach waits out Retry-After and resumes the same answer', async () => {
    const res = [sse([start, text]), json(429, 'rate_limited', { 'retry-after': '0' }), sse([start, text, end])];
    const fetchMock = vi.fn(async () => res.shift()!);
    vi.stubGlobal('fetch', fetchMock);
    const states: string[] = [];
    const out = await drain(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1 }), (st) => states.push(st));
    expect(out.map((e) => e.type)).toEqual(['message.start', 'part', 'message.end']); // replay overlap de-duplicated by seq
    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect(states).toContain('reconnecting');
    expect(states.at(-1)).toBe('live');
  });

  it('429 on re-attach without Retry-After backs off and retries', async () => {
    const res = [sse([start]), new Response('', { status: 429 }), sse([end])];
    vi.stubGlobal('fetch', vi.fn(async () => res.shift()!));
    const out = await drain(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1 }));
    expect(out.at(-1)).toMatchObject({ type: 'message.end', finishReason: 'stop' });
  });

  it('429 on re-attach with a Retry-After past the cap gives up as before ("Connection lost")', async () => {
    const res = [sse([start]), json(429, 'rate_limited', { 'retry-after': '600' })];
    vi.stubGlobal('fetch', vi.fn(async () => res.shift()!));
    await expect(drain(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1, maxRateWaitMs: 1000 }))).rejects.toThrow('replay 429');
  });

  it('repeated 429s on re-attach stop after maxRetries', async () => {
    const res = [sse([start]), ...Array.from({ length: 5 }, () => json(429, 'rate_limited', { 'retry-after': '0' }))];
    const fetchMock = vi.fn(async () => res.shift() ?? json(429, 'rate_limited'));
    vi.stubGlobal('fetch', fetchMock);
    await expect(drain(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1, maxRetries: 2 }))).rejects.toThrow('replay 429');
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(4); // bounded: first POST + at most maxRetries re-attaches + 1
  });

  it('useChat keeps the streamed text and shows the session-expired card', async () => {
    const res = [sse([start, text]), json(401, 'auth_expired')];
    const urls: string[] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string) => { urls.push(u); return res.shift() ?? json(404, 'not_found'); }));
    const { s } = session();
    const { result } = renderHook(() => useChat(new HttpAnalystAdapter('', s, { retryBaseMs: 1 }), new MemoryTranscriptStore()));
    act(() => { void result.current.send('q'); });
    await waitFor(() => expect((result.current.messages.at(-1) as AssistantMessage | undefined)?.parts?.some((p) => p.type === 'error')).toBe(true), { timeout: 3000 });
    expect(urls).toEqual(['/api/chat', '/api/chat/m1/stream']);
    const last = result.current.messages.at(-1) as AssistantMessage;
    expect(JSON.stringify(last.parts)).toContain('Denials rose 4% in Q3.');
    expect(last.parts.find((p) => p.type === 'error')).toMatchObject({ code: 'auth_expired', retryable: false });
    expect(JSON.stringify(result.current.messages)).not.toContain('Connection lost');
  });
});
