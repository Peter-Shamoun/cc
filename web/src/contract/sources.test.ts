import { describe, expect, it } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import type { MessageEnd, Source } from './v1.gen';
import { answerSources, exportFilename, exportUrl, sourcesForPart, sourcesForStep } from './sources';

const FILE = path.resolve(process.cwd(), '../api/contract/examples/answer.jsonl');

describe('citations + export (1.8.0)', () => {
  const end = fs.readFileSync(FILE, 'utf8').trim().split('\n').map((l) => JSON.parse(l) as { type: string })
    .find((e) => e.type === 'message.end') as MessageEnd;
  it('the answer example cites sem.claims and links its table', () => {
    expect(answerSources(end.sources).map((s) => s.name)).toEqual(['sem.claims']);
    const table = fs.readFileSync(FILE, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l) as { type: string; part?: { type: string; id: string } })
      .find((e) => e.type === 'part' && e.part?.type === 'table');
    expect(sourcesForPart(end.sources, table!.part!.id)).toHaveLength(1);
  });
  it('filters by role, part and step', () => {
    const s: Source[] = [
      { id: 'src_1', kind: 'view', name: 'sem.claims', label: 'Claims', role: 'answer', partIds: ['p_1'], stepIds: ['s_1'] },
      { id: 'src_2', kind: 'view', name: 'sem.payers', label: 'Payers', role: 'explored', stepIds: ['s_2'] },
    ];
    expect(answerSources(s)).toHaveLength(1);
    expect(sourcesForPart(s, 'p_9')).toEqual([]);
    expect(sourcesForStep(s, 's_2')[0].name).toBe('sem.payers');
    expect(answerSources(undefined)).toEqual([]);
  });
  it('builds export urls and filenames', () => {
    expect(exportUrl('c 1', 'json')).toBe('/api/conversations/c%201/export?format=json');
    expect(exportFilename('attachment; filename="claimclarity_conversation_2026-10-25.md"')).toBe('claimclarity_conversation_2026-10-25.md');
    expect(exportFilename(null, 'json')).toBe('claimclarity_conversation.json');
  });
});
