import { afterEach, describe, expect, it, vi } from 'vitest';
import { HttpAnalystAdapter, StreamGuardError, guardBody } from './http';

// Huge and stalled responses: a stream that goes silent, or one event that never ends, must not hang or eat the tab.
afterEach(() => { vi.unstubAllGlobals(); });
const enc = new TextEncoder();
const frame = (e: object) => enc.encode(`data: ${JSON.stringify(e)}\n\n`);
const start = { type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.13.0', seq: 0 };
const text = { type: 'part', part: { type: 'text', id: 't1', text: 'Denials rose 4% in Q3.' }, seq: 1 };
const end = { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 1 }, seq: 2 };
/** A body that sends `chunks`, then stays open forever (a hung proxy). Records whether it was cancelled. */
const hanging = (chunks: Uint8Array[], cancelled: { v: boolean }) => new Response(new ReadableStream<Uint8Array>({
  start(c) { for (const x of chunks) c.enqueue(x); },
  cancel() { cancelled.v = true; },
}), { status: 200 });
const closed = (chunks: Uint8Array[]) => new Response(new ReadableStream<Uint8Array>({ start(c) { for (const x of chunks) c.enqueue(x); c.close(); } }), { status: 200 });
const drain = async (a: HttpAnalystAdapter) => {
  const out: { type: string; part?: { code?: string; id?: string }; finishReason?: string; messageId?: string }[] = [];
  for await (const e of a.streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: 'q' }], signal: new AbortController().signal })) out.push(e as never);
  return out;
};
const opts = { retryBaseMs: 1, idleTimeoutMs: 40, maxFrameBytes: 4096 };

describe('guardBody', () => {
  it('passes a healthy body through untouched', async () => {
    const body = guardBody(closed([frame(start), frame(end)]).body!, opts);
    const t = await new Response(body).text();
    expect(t).toContain('message.start');
    expect(t).toContain('message.end');
  });

  it('errors a silent body as stalled and cancels the connection', async () => {
    const cancelled = { v: false };
    const r = guardBody(hanging([frame(start)], cancelled).body!, opts).getReader();
    await r.read();
    await expect(r.read()).rejects.toMatchObject({ reason: 'stalled' });
    expect(cancelled.v).toBe(true);
  });

  it('errors one line that grows past the cap without a break, even across chunks', async () => {
    const cancelled = { v: false };
    const big = enc.encode('data: "' + 'x'.repeat(3000));
    const r = guardBody(hanging([big, big], cancelled).body!, opts).getReader();
    await r.read();
    const err = await r.read().catch((e: unknown) => e);
    expect(err).toBeInstanceOf(StreamGuardError);
    expect((err as StreamGuardError).reason).toBe('too_large');
    expect(cancelled.v).toBe(true);
  });

  it('counts from the last line break, so many normal events never trip the cap', async () => {
    const many = Array.from({ length: 50 }, () => frame(text)); // ~5 KB total, each line far under 4 KB
    const t = await new Response(guardBody(closed(many).body!, opts)).text();
    expect(t.split('\n\n').length).toBeGreaterThan(50);
  });
});

describe('http adapter with a stalled or huge stream', () => {
  it('stalled before message.start: ends with a retryable timeout card, no endless spinner', async () => {
    const cancelled = { v: false };
    vi.stubGlobal('fetch', vi.fn(async () => hanging([], cancelled)));
    const out = await drain(new HttpAnalystAdapter('', undefined, opts));
    expect(out.map((e) => e.type)).toEqual(['part', 'message.end']);
    expect(out[0].part).toMatchObject({ code: 'timeout', id: 'err-stalled' });
    expect(cancelled.v).toBe(true);
  });

  it('stalled mid-answer: re-attaches (Last-Event-ID) and finishes the same answer', async () => {
    const cancelled = { v: false };
    const res = [hanging([frame(start), frame(text)], cancelled), closed([frame(start), frame(text), frame(end)])];
    const fetchMock = vi.fn(async () => res.shift()!);
    vi.stubGlobal('fetch', fetchMock);
    const out = await drain(new HttpAnalystAdapter('', undefined, opts));
    expect(out.map((e) => e.type)).toEqual(['message.start', 'part', 'message.end']);
    expect(out.at(-1)).toMatchObject({ finishReason: 'stop' });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    expect(cancelled.v).toBe(true);
  });

  it('an event past maxFrameBytes ends the answer with what arrived, without replaying the same event', async () => {
    const cancelled = { v: false };
    const fetchMock = vi.fn(async () => hanging([frame(start), frame(text), enc.encode('data: "' + 'y'.repeat(9000))], cancelled));
    vi.stubGlobal('fetch', fetchMock);
    const out = await drain(new HttpAnalystAdapter('', undefined, opts));
    expect(out.map((e) => e.type)).toEqual(['message.start', 'part', 'part', 'message.end']);
    expect(out[2].part).toMatchObject({ id: 'err-too-large' });
    expect(out[3]).toMatchObject({ messageId: 'm1', finishReason: 'error' });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('a large but complete event under the cap is delivered (no false positives)', async () => {
    const bigText = { type: 'part', part: { type: 'text', id: 't2', text: 'z'.repeat(200_000) }, seq: 1 };
    vi.stubGlobal('fetch', vi.fn(async () => closed([frame(start), frame(bigText), frame(end)])));
    const out = await drain(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1 }));
    expect(out.map((e) => e.type)).toEqual(['message.start', 'part', 'message.end']);
  });
});
