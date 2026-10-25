import { describe, expect, it } from 'vitest';
import { MockAnalystAdapter } from './mock';
import { MemoryTranscriptStore } from './memoryStore';
import { parseSse } from './http';
import { FIXTURES } from '../fixtures';
import { KNOWN_PART_TYPES, newAssistantView, reduce, type StreamEvent } from '../lib/contract';

async function collect(it: AsyncIterable<StreamEvent>) {
  const out: StreamEvent[] = [];
  for await (const e of it) out.push(e);
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

  it('stops on abort', async () => {
    const a = new MockAnalystAdapter(5);
    const ctrl = new AbortController();
    const evs: StreamEvent[] = [];
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
});
