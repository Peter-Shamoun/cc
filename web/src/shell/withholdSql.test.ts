import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { SharedConversation } from '../contract/v1.gen';
import { withholdSql } from './withholdSql';

const example = JSON.parse(readFileSync(resolve(process.cwd(), '../api/contract/examples/share.json'), 'utf8')).view.response as SharedConversation;

const synthetic = {
  shareId: 'sh_x', title: 'Synthetic', updatedAt: null, includeData: false, createdAt: '2026-10-25T20:00:00Z',
  messages: [{ id: 'm_1', role: 'assistant', createdAt: '2026-10-25T20:00:00Z', parts: [
    { type: 'text', id: 'p_1', text: 'Paid by month.' },
    { type: 'code', id: 'p_2', language: 'SQL', code: "SELECT 1 FROM sem.claims WHERE member_id = 'M0001'", role: 'query' },
    { type: 'code', id: 'p_5', language: 'python', code: 'print(1)' },
    { type: 'table', id: 'p_3', title: 'Result', columns: [], rows: [], rowCount: 3, sqlPartId: 'p_2', dataWithheld: true },
    { type: 'tool_call', id: 'p_4', tool: 'sql', label: 'Query', state: 'ok', input: { sql: 'SELECT 2', purpose: 'answer' } },
  ], steps: [
    { type: 'step.sql_running', stepId: 's_1', sql: 'SELECT 3', purpose: 'answer', attempt: 1, seq: 1 },
    { type: 'step.repair_attempt', stepId: 's_2', failedStepId: 's_1', attempt: 1, error: { code: 'x', message: 'bad' }, revisedSql: 'SELECT 4', seq: 2 },
    { type: 'step.candidates', stepId: 's_3', candidates: [{ candidateId: 'c_1', sql: 'SELECT 5', ok: true }], chosenId: 'c_1', method: 'vote', seq: 3 },
  ] }],
} as unknown as SharedConversation;

describe('withholdSql (QA-S18)', () => {
  it('a data-off share loses every SQL string; prose, tables and other code stay', () => {
    // 1.15.0: the server already withholds SQL in the example (sqlWithheld), so this stays a no-op safety net there
    expect(JSON.stringify(withholdSql(example))).not.toMatch(/SELECT/);
    for (const shared of [synthetic]) {
      expect(JSON.stringify(shared)).toMatch(/SELECT/);
      const out = withholdSql(shared);
      expect(JSON.stringify(out)).not.toMatch(/SELECT/);
      expect(out.messages).toHaveLength(shared.messages.length);
    }
    const parts = (withholdSql(synthetic).messages[0] as unknown as { parts: { type: string; id: string; sqlPartId?: string }[] }).parts;
    expect(parts.map((p) => p.id)).toEqual(['p_1', 'p_5', 'p_3', 'p_4']);
    expect(parts.find((p) => p.type === 'table')).not.toHaveProperty('sqlPartId');
    const steps = (withholdSql(synthetic).messages[0] as unknown as { steps: Record<string, unknown>[] }).steps;
    expect(steps.map((s) => s.type)).toEqual(['step.sql_running', 'step.repair_attempt', 'step.candidates']);
  });

  it('does not touch the input, and leaves a with-data share alone', () => {
    const before = JSON.stringify(synthetic);
    withholdSql(synthetic);
    expect(JSON.stringify(synthetic)).toBe(before);
    const on = { ...synthetic, includeData: true };
    expect(withholdSql(on)).toBe(on);
  });
});
