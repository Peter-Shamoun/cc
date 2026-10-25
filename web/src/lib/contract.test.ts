import { describe, expect, it } from 'vitest';
import { newAssistantView, reduce, type StreamEvent } from './contract';

const run = (events: StreamEvent[]) => events.reduce(reduce, newAssistantView('a1'));

describe('stream reducer (spec §5)', () => {
  it('appends text deltas and tracks the open caret', () => {
    const m = run([
      { type: 'message.start', messageId: 'm1', conversationId: 'c1' },
      { type: 'text.start', partId: 't1' },
      { type: 'text.delta', partId: 't1', delta: 'Hello ' },
      { type: 'text.delta', partId: 't1', delta: 'world' },
    ]);
    expect(m.id).toBe('m1');
    expect(m.parts).toEqual([{ type: 'text', id: 't1', text: 'Hello world' }]);
    expect(m.openText).toEqual(['t1']);
    expect(reduce(m, { type: 'text.end', partId: 't1' }).openText).toEqual([]);
  });

  it('keeps first-appearance order and replaces parts by id', () => {
    const m = run([
      { type: 'part', part: { type: 'code', id: 'q', language: 'sql', code: 'SELECT 1' } },
      { type: 'text.delta', partId: 't', delta: 'x' },
      { type: 'part', part: { type: 'code', id: 'q', language: 'sql', code: 'SELECT 2' } },
    ]);
    expect(m.parts.map((p) => p.id)).toEqual(['q', 't']);
    expect(m.parts[0]).toMatchObject({ code: 'SELECT 2' });
  });

  it('patches tool calls from running to ok', () => {
    const m = run([
      { type: 'part', part: { type: 'tool_call', id: 'tc', tool: 'run_sql', label: 'Queried', state: 'running' } },
      { type: 'part.patch', partId: 'tc', patch: { state: 'ok', durationMs: 12 } as never },
    ]);
    expect(m.parts[0]).toMatchObject({ type: 'tool_call', state: 'ok', durationMs: 12 });
  });

  it('builds reasoning and code from deltas, handles status and end', () => {
    let m = run([
      { type: 'status', label: 'Planning…' },
      { type: 'reasoning.delta', partId: 'r', delta: 'think ' },
      { type: 'reasoning.delta', partId: 'r', delta: 'more' },
      { type: 'reasoning.end', partId: 'r', durationMs: 3000 },
      { type: 'code.delta', partId: 'c', delta: 'SELECT' },
    ]);
    expect(m.status).toBe('Planning…');
    expect(m.parts[0]).toMatchObject({ type: 'reasoning', summary: 'think more', durationMs: 3000 });
    expect(m.parts[1]).toMatchObject({ type: 'code', code: 'SELECT' });
    m = reduce(m, { type: 'message.end', messageId: 'a1', finishReason: 'stop', usage: { queries: 1, elapsedMs: 5 } });
    expect(m.streaming).toBe(false);
    expect(m.status).toBeNull();
  });

  it('keeps unknown part types for the unsupported fallback', () => {
    const m = run([{ type: 'part', part: { type: 'hologram', id: 'h' } as never }]);
    expect(m.parts).toHaveLength(1);
  });
});
