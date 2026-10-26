import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook, waitFor } from '@testing-library/react';
import { createTableReveal } from './tableReveal';
import { useChat } from './useChat';
import { MemoryTranscriptStore } from '../adapters/memoryStore';
import type { AnalystAdapter, AnyStreamEvent } from '../adapters/types';
import type { AssistantViewV1, Part, TablePart } from '../api/contract';

const table = (id: string, n: number): TablePart => ({ type: 'table', id, columns: [{ key: 'payer', label: 'Payer', type: 'string' }], rows: Array.from({ length: n }, (_, i) => [`Payer ${i}`]), rowCount: n });
const rowsOf = (parts: Part[], id: string) => (parts.find((p) => p.id === id) as TablePart).rows.length;

describe('createTableReveal', () => {
  it('grows a big table a few rows per frame, bounded by maxFrames, then shows all', () => {
    const r = createTableReveal({ maxFrames: 4 });
    const parts: Part[] = [{ type: 'text', id: 't', text: 'x' }, table('tb', 10)];
    expect(r.note(parts)).toBe(true);
    const seen = [rowsOf(r.apply(parts), 'tb')];
    while (r.step()) seen.push(rowsOf(r.apply(parts), 'tb'));
    seen.push(rowsOf(r.apply(parts), 'tb'));
    expect(seen).toEqual([3, 6, 9, 10]);
    expect(r.active).toBe(false);
    expect(r.apply(parts)).toBe(parts); // nothing revealing: same array, no re-render churn
  });

  it('small tables appear whole; a re-sent part does not restart', () => {
    const r = createTableReveal({ minRows: 3 });
    expect(r.note([table('small', 3)])).toBe(false);
    const big = [table('big', 50)];
    r.note(big);
    while (r.step());
    expect(r.note(big)).toBe(false);
    expect(rowsOf(r.apply(big), 'big')).toBe(50);
  });

  it('finish() shows everything at once (Stop / end of stream)', () => {
    const r = createTableReveal();
    const parts = [table('tb', 100)];
    r.note(parts);
    expect(rowsOf(r.apply(parts), 'tb')).toBe(5);
    r.finish();
    expect(rowsOf(r.apply(parts), 'tb')).toBe(100);
  });

  it('never mutates the input part', () => {
    const r = createTableReveal();
    const parts = [table('tb', 40)];
    r.note(parts);
    r.apply(parts);
    expect(parts[0].rows).toHaveLength(40);
  });
});

describe('useChat: partial-table streaming', () => {
  afterEach(() => vi.unstubAllGlobals());

  function gated(n: number) {
    let release!: () => void;
    const gate = new Promise<void>((r) => { release = r; });
    const adapter = {
      name: 'fake',
      async *streamTurn() {
        yield { type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.0.0', seq: 0 } as unknown as AnyStreamEvent;
        yield { type: 'part', part: table('tb', n), seq: 1 } as unknown as AnyStreamEvent;
        await gate;
        yield { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 1, elapsedMs: 5 }, seq: 2 } as unknown as AnyStreamEvent;
      },
      fetchBlob: async () => new Blob(),
    } as unknown as AnalystAdapter;
    return { adapter, release: () => release() };
  }

  it('a table that arrives mid-stream grows to full; the stored message always has every row', async () => {
    const { adapter, release } = gated(48);
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    const counts: number[] = [];
    let done!: Promise<void>;
    act(() => { done = result.current.send('q'); });
    const rows = () => { const m = result.current.messages[1] as AssistantViewV1 | undefined; const t = m?.parts.find((p) => p.type === 'table') as TablePart | undefined; return t?.rows.length; };
    await waitFor(() => { const n = rows(); if (n !== undefined) counts.push(n); expect(n).toBe(48); }, { timeout: 3000, interval: 5 });
    expect(counts[0]).toBeLessThan(48); // it did not land all at once
    await act(async () => { release(); await done; });
    expect(rows()).toBe(48);
    const saved = await store.getConversation(result.current.convId!);
    const savedTable = (saved!.messages[1] as unknown as AssistantViewV1).parts.find((p) => p.type === 'table') as TablePart;
    expect(savedTable.rows).toHaveLength(48);
  });

  it('a table in the last events (message.end right after) still grows, and the turn waits for it', async () => {
    const adapter = {
      name: 'fake',
      async *streamTurn() {
        yield { type: 'message.start', messageId: 'm1', conversationId: 'c', contractVersion: '1.0.0', seq: 0 } as unknown as AnyStreamEvent;
        yield { type: 'part', part: table('tb', 24), seq: 1 } as unknown as AnyStreamEvent;
        yield { type: 'message.end', messageId: 'm1', finishReason: 'stop', usage: { queries: 1, elapsedMs: 5 }, seq: 2 } as unknown as AnyStreamEvent;
      },
      fetchBlob: async () => new Blob(),
    } as unknown as AnalystAdapter;
    const store = new MemoryTranscriptStore();
    const renders: { rows?: number; busy: boolean }[] = [];
    const { result } = renderHook(() => {
      const c = useChat(adapter, store);
      const t = (c.messages[1] as AssistantViewV1 | undefined)?.parts.find((p) => p.type === 'table') as TablePart | undefined;
      renders.push({ rows: t?.rows.length, busy: c.busy });
      return c;
    });
    // Outside act on purpose: act would batch the per-frame renders this test is about.
    const quiet = vi.spyOn(console, 'error').mockImplementation(() => {});
    await result.current.send('q');
    await waitFor(() => expect(result.current.busy).toBe(false));
    quiet.mockRestore();
    const seen = renders.filter((r) => r.rows !== undefined);
    expect(new Set(seen.map((r) => r.rows)).size).toBeGreaterThan(2); // it grew
    expect(seen.filter((r) => r.rows! < 24).every((r) => r.busy)).toBe(true); // can't send over a half-shown table
    expect(seen.at(-1)).toEqual({ rows: 24, busy: false });
  });

  it('reduced motion: the whole table at once', async () => {
    vi.stubGlobal('matchMedia', (q: string) => ({ matches: q.includes('reduce'), media: q, addEventListener() {}, removeEventListener() {} }));
    const { adapter, release } = gated(48);
    const store = new MemoryTranscriptStore();
    const { result } = renderHook(() => useChat(adapter, store));
    let done!: Promise<void>;
    act(() => { done = result.current.send('q'); });
    await waitFor(() => expect((result.current.messages[1] as AssistantViewV1 | undefined)?.parts.length).toBe(1));
    expect(((result.current.messages[1] as AssistantViewV1).parts[0] as TablePart).rows).toHaveLength(48);
    await act(async () => { release(); await done; });
  });
});
