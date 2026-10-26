import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, screen } from '@testing-library/react';
import { newAssistantView, reduce, type AssistantView, type StreamEvent } from '../../lib/contract';
import { AssistantTurn, messageMarkdown } from '../Message';

vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
Element.prototype.scrollTo = function () {} as never;
const noop = () => {};

// Wire data the server should never send but a bug, a proxy or a newer server could: every part type with
// missing, null or wrong-typed fields. The rest of the answer must still render and Copy must not throw.
const MALFORMED: Record<string, unknown>[] = [
  { type: 'text', id: 'm1', text: null },
  { type: 'text', id: 'm2', text: 42 },
  { type: 'table', id: 'm3' },
  { type: 'table', id: 'm4', columns: null, rows: null },
  { type: 'table', id: 'm5', columns: [{ key: 'a' }], rows: [null, 'x', [1, 2, 3], { a: 1 }] },
  { type: 'table', id: 'm6', columns: 'nope', rows: [[1]], rowCount: 'many' },
  { type: 'chart', id: 'm7' },
  { type: 'chart', id: 'm8', spec: null, data: null },
  { type: 'code', id: 'm9' },
  { type: 'code', id: 'm10', code: null, language: null },
  { type: 'callout', id: 'm11' },
  { type: 'callout', id: 'm12', items: null, variant: 'nope' },
  { type: 'image', id: 'm13' },
  { type: 'file', id: 'm14' },
  { type: 'error', id: 'm15' },
  { type: 'abstain', id: 'm16' },
  { type: 'abstain', id: 'm17', suggestions: null, reason: null, category: 42 },
  { type: 'clarification', id: 'm18' },
  { type: 'clarification', id: 'm19', choices: 'a,b', question: null },
  { type: 'artifact', id: 'm20' },
  { type: 'reasoning', id: 'm21', text: null },
  { type: 'tool_call', id: 'm22' },
  { type: 'tool_call', id: 'm23', name: null, input: 'x', output: 7 },
  { type: 'totally_new_part', id: 'm24', payload: {} },
  { id: 'm25' },
];

const viewWith = (bad: Record<string, unknown>): AssistantView => {
  let v = newAssistantView('a1');
  v = reduce(v, { type: 'part', part: { type: 'text', id: 'ok1', text: 'Before the odd part.' } } as StreamEvent);
  v = reduce(v, { type: 'part', part: bad } as unknown as StreamEvent);
  v = reduce(v, { type: 'part', part: { type: 'text', id: 'ok2', text: 'After the odd part.' } } as StreamEvent);
  return reduce(v, { type: 'message.end', messageId: 'a1', finishReason: 'stop', usage: { queries: 0, elapsedMs: 10 } as never });
};

let quiet: ReturnType<typeof vi.spyOn>;
beforeEach(() => { quiet = vi.spyOn(console, 'error').mockImplementation(() => {}); });
afterEach(() => { cleanup(); quiet.mockRestore(); });

describe('malformed parts never take the answer down', () => {
  for (const bad of MALFORMED) {
    it(`${String(bad.type ?? 'no type')} ${JSON.stringify(bad).slice(0, 60)}`, () => {
      const v = viewWith(bad);
      render(<AssistantTurn msg={v} isLast onOpen={noop} onRegenerate={noop} onEdit={noop} />);
      expect(screen.getByText('Before the odd part.')).toBeTruthy();
      expect(screen.getByText('After the odd part.')).toBeTruthy();
      expect(() => messageMarkdown(v)).not.toThrow();
      expect(messageMarkdown(v)).toContain('After the odd part.');
    });
  }
});

describe('sanitizeParts', async () => {
  const { sanitizeParts } = await import('./sanitizeParts');
  it('derives columns from rows, maps object rows, drops typeless parts, keeps unknown types', () => {
    const out = sanitizeParts([
      { type: 'table', id: 't', rows: [{ payer: 'Aetna', n: 2 }, 'junk'] },
      { id: 'x' }, null, 'str',
      { type: 'future_part', id: 'f' },
    ]);
    expect(out.map((p) => p.type)).toEqual(['table', 'future_part']);
    const t = out[0] as unknown as { columns: { key: string }[]; rows: unknown[][]; rowCount: number };
    expect(t.columns.map((c) => c.key)).toEqual(['payer', 'n']);
    expect(t.rows).toEqual([['Aetna', 2]]);
    expect(t.rowCount).toBe(1);
  });
  it('is memoized per parts array and returns [] for non-arrays', () => {
    const a = [{ type: 'text', id: 'a', text: 'x' }];
    expect(sanitizeParts(a)).toBe(sanitizeParts(a));
    expect(sanitizeParts(null)).toEqual([]);
  });
});
