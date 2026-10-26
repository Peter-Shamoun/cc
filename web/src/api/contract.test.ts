import { describe, expect, it } from 'vitest';
import { CONTRACT_VERSION, newAssistantViewV1, reduceV1, type StreamEvent } from './contract';

const run: StreamEvent[] = [
  { type: 'message.start', seq: 0, messageId: 'm1', conversationId: 'c1', contractVersion: CONTRACT_VERSION },
  { type: 'step.sql_running', seq: 1, stepId: 's1', sql: 'SELECT 1', purpose: 'answer', attempt: 1 },
  { type: 'step.sql_result', seq: 2, stepId: 's1', ok: false, elapsedMs: 3, error: { code: 'binder_error', message: 'x' } },
  { type: 'step.repair_attempt', seq: 3, stepId: 's2', failedStepId: 's1', attempt: 1, error: { code: 'binder_error', message: 'x' }, revisedSql: 'SELECT 2' },
  { type: 'step.sql_running', seq: 4, stepId: 's3', sql: 'SELECT 2', purpose: 'repair', attempt: 2, repairOf: 's2' },
  { type: 'escalation', seq: 5, from: { model: 'haiku' }, to: { model: 'sonnet' }, reason: 'r', trigger: 'repair_exhausted' },
  { type: 'step.sql_result', seq: 6, stepId: 's3', ok: true, elapsedMs: 5, rowCount: 1 },
  { type: 'heartbeat' },
  { type: 'text.start', seq: 7, partId: 't' },
  { type: 'text.delta', seq: 8, partId: 't', delta: 'Hello' },
  { type: 'text.end', seq: 9, partId: 't' },
  { type: 'part', seq: 10, part: { type: 'abstain', id: 'a', category: 'low_confidence', reason: 'no', suggestions: [] } },
  { type: 'message.end', seq: 11, messageId: 'm1', finishReason: 'abstain', usage: { queries: 2, elapsedMs: 9, costUsd: 0.01, costIsEstimate: true } },
];

describe('reduceV1', () => {
  it('groups steps by stepId, tracks escalation, parts and finish', () => {
    const v = run.reduce(reduceV1, newAssistantViewV1('tmp'));
    expect(v.id).toBe('m1');
    expect(v.contractVersion).toBe(CONTRACT_VERSION);
    expect(v.steps.map((s) => [s.stepId, s.kind, s.state])).toEqual([
      ['s1', 'sql', 'error'], ['s2', 'repair', 'ok'], ['s3', 'sql', 'ok'], ['esc-0', 'escalation', 'ok'],
    ]);
    expect(v.escalations).toHaveLength(1);
    expect(v.parts.map((p) => p.type)).toEqual(['text', 'abstain']);
    expect((v.parts[0] as { text: string }).text).toBe('Hello');
    expect(v.finishReason).toBe('abstain');
    expect(v.usage?.costUsd).toBe(0.01);
    expect(v.streaming).toBe(false);
  });

  it('drops replayed events (seq <= lastSeq) after a resume', () => {
    const once = run.reduce(reduceV1, newAssistantViewV1('tmp'));
    const twice = run.slice(8, 11).reduce(reduceV1, once);
    expect((twice.parts[0] as { text: string }).text).toBe('Hello');
  });

  it('ignores unknown events', () => {
    const v = reduceV1(newAssistantViewV1('x'), { type: 'step.future_thing', seq: 0 } as never);
    expect(v.steps).toHaveLength(0);
  });
});
