import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import type { ClarifyAnswer, ClarifyQuestion } from '../api/contract';
import { clarifyRequest, clarifySummary, validateClarifyAnswers } from './clarify';

// 1.23.0: the shared cases (api/contract/clarify_cases.json) also run in Python (api/tests/test_clarify_multi_123.py).
const API = resolve(__dirname, '../../../api/contract');
const cases = JSON.parse(readFileSync(resolve(API, 'clarify_cases.json'), 'utf8')) as {
  questions: ClarifyQuestion[];
  answers: { name: string; answers: ClarifyAnswer[]; error: string | null; summary: string | null }[];
};
const load = (name: string) => readFileSync(resolve(API, 'examples/edge', `${name}.jsonl`), 'utf8').trim().split('\n')
  .map((l) => JSON.parse(l) as { type: string; part?: { type: string; questions?: ClarifyQuestion[] }; resumeOf?: unknown });

describe('multi-question clarification agrees with Python (clarify_cases.json)', () => {
  it.each(cases.answers)('$name', ({ answers, error, summary }) => {
    expect(validateClarifyAnswers(cases.questions, answers)).toBe(error);
    if (error === null) expect(clarifySummary(cases.questions, answers)).toBe(summary);
  });

  it('every case question has the Claude shape', () => {
    for (const q of cases.questions) {
      expect(Array.from(q.header).length).toBeLessThanOrEqual(12);
      expect(q.options.length).toBeGreaterThanOrEqual(2);
      expect(q.options.length).toBeLessThanOrEqual(4);
      for (const o of q.options) expect(o.description.length).toBeGreaterThan(0);
    }
  });

  it('clarifyRequest copies the answers', () => {
    const a: ClarifyAnswer[] = [{ questionId: 'metric', choiceIds: ['count'] }];
    const r = clarifyRequest('q_1', a);
    expect(r).toEqual({ clarificationId: 'q_1', answers: a });
    expect(r.answers[0]).not.toBe(a[0]);
  });

  it('the clarify_multi demo streams carry the questions, then answer on resume', () => {
    const first = load('clarify_multi');
    const part = first.find((e) => e.type === 'part' && e.part?.type === 'clarification')?.part;
    expect(part?.questions?.map((q) => q.header)).toEqual(['Metric', 'Period', 'Group by']);
    expect(part?.questions?.[2].multiSelect).toBe(true);
    const second = load('clarify_multi_2');
    expect(second[0].resumeOf).toBeTruthy();
    expect(second.some((e) => e.type === 'part' && e.part?.type === 'table')).toBe(true);
  });
});
