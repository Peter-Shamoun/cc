import type { Part } from '../lib/contract';
import { CONTRACT_VERSION, type Part as V1Part } from '../api/contract';
import { BLOBS, pickFixture, type FixtureStep } from '../fixtures';
import type { AnalystAdapter, AnyStreamEvent } from './types';

const sleep = (ms: number, signal: AbortSignal) =>
  new Promise<void>((resolve, reject) => {
    if (signal.aborted) return reject(new DOMException('aborted', 'AbortError'));
    if (ms <= 0) return resolve();
    const t = setTimeout(resolve, ms);
    signal.addEventListener('abort', () => {
      clearTimeout(t);
      reject(new DOMException('aborted', 'AbortError'));
    }, { once: true });
  });

/** Split text into small word-ish chunks to mimic token streaming. */
export function chunkText(text: string): string[] {
  return text.match(/\S+\s*|\s+/g) ?? [];
}

/**
 * Replays synthetic fixture conversations as spec §5 StreamEvents.
 * Fully offline: no network, no paid APIs. `delayMs` = per-chunk delay (0 in tests).
 */
export class MockAnalystAdapter implements AnalystAdapter {
  readonly name = 'mock';
  constructor(private delayMs = 16) {}

  async *streamTurn({ conversationId, messages, signal }: Parameters<AnalystAdapter['streamTurn']>[0]): AsyncIterable<AnyStreamEvent> {
    const prompt = [...messages].reverse().find((m) => m.role === 'user')?.text ?? '';
    const fixture = pickFixture(prompt);
    const messageId = `m_${Math.random().toString(36).slice(2, 10)}`;
    let seq = 0;
    // Every event carries a monotonically increasing seq, as the v1 server does (CONTRACT.md §3).
    const stamp = (e: object) => ({ ...e, seq: seq++ }) as unknown as AnyStreamEvent;
    yield stamp({ type: 'message.start', messageId, conversationId, contractVersion: CONTRACT_VERSION, strategy: 'mock-replay' });
    const t0 = Date.now();
    try {
      for (const step of fixture.steps) for await (const e of this.emit(step, signal)) yield stamp(e);
      const u = fixture.usage;
      const elapsedMs = u?.latency?.totalMs ?? Date.now() - t0;
      yield stamp({ type: 'message.end', messageId, finishReason: fixture.finishReason ?? 'stop', usage: { ...u, queries: fixture.queries, elapsedMs } });
    } catch (e) {
      if ((e as Error).name !== 'AbortError') throw e;
      yield stamp({ type: 'message.end', messageId, finishReason: 'aborted', usage: { queries: 0, elapsedMs: Date.now() - t0 } });
    }
  }

  async fetchBlob(id: string): Promise<Blob> {
    const b = BLOBS[id];
    if (!b) throw new Error('blob not found');
    return new Blob([b.content], { type: b.mime });
  }

  private async *emit(step: FixtureStep, signal: AbortSignal): AsyncIterable<object> {
    if ('event' in step) {
      // Raw contract-v1 step/escalation event; waitMs simulates the step's own latency.
      await sleep(step.waitMs !== undefined ? (this.delayMs ? step.waitMs : 0) : this.delayMs * 10, signal);
      yield step.event;
      return;
    }
    if ('status' in step) {
      yield { type: 'status', label: step.status };
      await sleep(this.delayMs * 12, signal);
      return;
    }
    const part = step as Part | Exclude<V1Part, Part>;
    switch (part.type) {
      case 'text':
        yield { type: 'text.start', partId: part.id };
        for (const c of chunkText(part.text)) {
          await sleep(this.delayMs, signal);
          yield { type: 'text.delta', partId: part.id, delta: c };
        }
        yield { type: 'text.end', partId: part.id };
        return;
      case 'reasoning':
        for (const c of chunkText(part.summary)) {
          await sleep(this.delayMs / 2, signal);
          yield { type: 'reasoning.delta', partId: part.id, delta: c };
        }
        yield { type: 'reasoning.end', partId: part.id, durationMs: part.durationMs ?? 1000 };
        return;
      case 'tool_call': {
        const { output, durationMs, ...rest } = part;
        yield { type: 'part', part: { ...rest, state: 'running' } };
        await sleep(this.delayMs * 25, signal);
        yield { type: 'part.patch', partId: part.id, patch: { state: part.state, output, durationMs } };
        return;
      }
      default:
        await sleep(this.delayMs * 6, signal);
        yield { type: 'part', part };
    }
  }
}
