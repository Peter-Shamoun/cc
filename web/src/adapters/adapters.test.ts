import { afterEach, describe, expect, it, vi } from 'vitest';
import { MockAnalystAdapter } from './mock';
import { MemoryTranscriptStore } from './memoryStore';
import { HttpAnalystAdapter, parseSse } from './http';
import { SseDecoder } from '../contract/sse';
import { FIXTURES } from '../fixtures';
import { KNOWN_PART_TYPES, newAssistantView, reduce, type StreamEvent } from '../lib/contract';
import { newAssistantViewV1, reduceV1 } from '../api/contract';
import { V1_FIXTURES } from '../fixtures/v1';
import type { AnyStreamEvent } from './types';

async function collect(it: AsyncIterable<AnyStreamEvent>) {
  const out: StreamEvent[] = [];
  for await (const e of it) out.push(e as StreamEvent);
  return out;
}

describe('MockAnalystAdapter', () => {
  it('fixtures cover every renderable part type', () => {
    const types = new Set(FIXTURES.flatMap((f) => f.steps.filter((s) => 'type' in s).map((s) => (s as { type: string }).type)));
    for (const t of KNOWN_PART_TYPES.filter((t) => t !== 'status')) expect(types, t).toContain(t);
  });

  it('replays a fixture into a complete message', async () => {
    const a = new MockAnalystAdapter(0);
    for (const f of FIXTURES) {
      const evs = await collect(a.streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: f.prompt }], signal: new AbortController().signal }));
      expect(evs[0].type).toBe('message.start');
      expect(evs.at(-1)).toMatchObject({ type: 'message.end', finishReason: 'stop' });
      const m = evs.reduce(reduce, newAssistantView('x'));
      expect(m.streaming).toBe(false);
      expect(m.parts.length).toBe(f.steps.filter((s) => 'type' in s).length);
      expect(m.parts.filter((p) => p.type === 'tool_call').every((p) => p.type === 'tool_call' && p.state !== 'running')).toBe(true);
    }
  });

  it('replays answer fixtures as contract-v1 streams (seq, steps, finishReason)', async () => {
    const a = new MockAnalystAdapter(0);
    for (const f of V1_FIXTURES) {
      const evs = await collect(a.streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: f.prompt }], signal: new AbortController().signal }));
      const seqs = evs.map((e) => (e as { seq?: number }).seq);
      expect(seqs).toEqual(seqs.map((_, i) => i));
      const m = (evs as AnyStreamEvent[]).reduce(reduceV1, newAssistantViewV1('x'));
      expect(m.finishReason).toBe(f.finishReason ?? 'stop');
      expect(m.steps.every((s) => s.state !== 'running')).toBe(true);
      expect(m.steps.length).toBe(new Set(f.steps.flatMap((s) => ('event' in s ? [(s.event as { stepId?: string }).stepId] : []))).size);
    }
  });

  it('stops on abort', async () => {
    const a = new MockAnalystAdapter(5);
    const ctrl = new AbortController();
    const evs: AnyStreamEvent[] = [];
    for await (const e of a.streamTurn({ conversationId: 'c', messages: [{ id: 'u', role: 'user', text: FIXTURES[0].prompt }], signal: ctrl.signal })) {
      evs.push(e);
      if (evs.length === 3) ctrl.abort();
    }
    expect(evs.at(-1)).toMatchObject({ type: 'message.end', finishReason: 'aborted' });
  });

  it('serves blobs by opaque id', async () => {
    const b = await new MockAnalystAdapter(0).fetchBlob('file_denial_q3');
    expect(b.type).toContain('text/csv');
  });
});

describe('MemoryTranscriptStore', () => {
  it('stores in memory and never touches localStorage', async () => {
    localStorage.clear();
    const s = new MemoryTranscriptStore();
    await s.appendMessage('c1', { id: 'u1', role: 'user', text: 'denial rate', createdAt: new Date().toISOString() }, 'Denial rate');
    await s.appendMessage('c1', { id: 'a1', role: 'assistant', parts: [], createdAt: new Date().toISOString() });
    expect((await s.listConversations()).items[0]).toMatchObject({ id: 'c1', title: 'Denial rate' });
    await s.truncateAfter('c1', 'u1');
    expect((await s.getConversation('c1'))?.messages).toHaveLength(1);
    await s.deleteConversation('c1');
    expect((await s.listConversations()).items).toHaveLength(0);
    expect(localStorage.length).toBe(0);
  });
});

const bodyOf = (...chunks: string[]) => {
  const enc = new TextEncoder();
  return new ReadableStream<Uint8Array>({ start(c) { chunks.forEach((x) => c.enqueue(enc.encode(x))); c.close(); } });
};

describe('parseSse', () => {
  it('parses data lines split across chunks', async () => {
    const enc = new TextEncoder();
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        c.enqueue(enc.encode('data: {"type":"text.delta","partId":"t","del'));
        c.enqueue(enc.encode('ta":"hi"}\n\n: comment\n\ndata: {"type":"heartbeat"}\n\n'));
        c.close();
      },
    });
    const evs = await collect(parseSse(body));
    expect(evs).toEqual([{ type: 'text.delta', partId: 't', delta: 'hi' }, { type: 'heartbeat' }]);
  });

  it('skips and counts malformed frames instead of throwing (contract 1.12 fail-safe)', async () => {
    const dec = new SseDecoder();
    const body = bodyOf(
      'data: {"type":"text.delta","partId":"t","delta":"a"}\n\n',
      'data: {"type":"text.delta","partId":"t","delta":\n\n', // truncated JSON
      'data: [1,2]\n\ndata: {"no":"type"}\n\ndata: not json at all\n\n',
      'data: {"type":"text.delta","partId":"t","delta":"b"}\n\n',
    );
    const evs = await collect(parseSse(body, dec));
    expect(evs.map((e) => (e as { delta?: string }).delta)).toEqual(['a', 'b']);
    expect(dec.malformed).toBe(4);
  });

  it('accepts CRLF and lone-CR line endings, a CRLF split across chunks, and a final frame with no blank line', async () => {
    const body = bodyOf('id: m1:0\r\ndata: {"type":"heartbeat"}\r', '\n\r\n', 'data: {"type":"text.delta","partId":"t","delta":"x"}\r\rdata: {"type":"heartbeat","n":2}');
    expect(await collect(parseSse(body))).toEqual([{ type: 'heartbeat' }, { type: 'text.delta', partId: 't', delta: 'x' }, { type: 'heartbeat', n: 2 }]);
  });
});

describe('http adapter: a malformed frame mid-answer', () => {
  afterEach(() => vi.unstubAllGlobals());
  it('keeps the live answer going on one connection, with no reconnect', async () => {
    const ev = (o: object) => `data: ${JSON.stringify(o)}\n\n`;
    const calls: unknown[] = [];
    vi.stubGlobal('fetch', vi.fn(async (u: string) => {
      calls.push(u);
      return new Response(bodyOf(
        ev({ type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.12.0', seq: 0 }),
        ev({ type: 'text.start', partId: 't1', seq: 1 }),
        'data: {"type":"text.delta","partId":"t1","del\n\n', // garbage from a proxy
        ev({ type: 'text.delta', partId: 't1', delta: 'ok', seq: 2 }),
        ev({ type: 'text.end', partId: 't1', seq: 3 }),
        ev({ type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 1, elapsedMs: 1 }, seq: 4 }),
      ), { status: 200, headers: { 'content-type': 'text/event-stream' } });
    }));
    const turn = { conversationId: 'c', messages: [{ id: 'u', role: 'user' as const, text: 'q' }], signal: new AbortController().signal };
    const out = (await collect(new HttpAnalystAdapter('', undefined, { retryBaseMs: 1 }).streamTurn(turn))) as unknown as { type: string; seq: number }[];
    expect(calls).toHaveLength(1);
    expect(out.map((e) => e.seq)).toEqual([0, 1, 2, 3, 4]);
    expect(out.at(-1)!.type).toBe('message.end');
  });
});
