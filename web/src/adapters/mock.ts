import type { Part, StreamEvent } from '../lib/contract';
import { BLOBS, pickFixture, type FixtureStep } from '../fixtures';
import type { AnalystAdapter } from './types';

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

  async *streamTurn({ conversationId, messages, signal }: Parameters<AnalystAdapter['streamTurn']>[0]): AsyncIterable<StreamEvent> {
    const prompt = [...messages].reverse().find((m) => m.role === 'user')?.text ?? '';
    const fixture = pickFixture(prompt);
    const messageId = `m_${Math.random().toString(36).slice(2, 10)}`;
    yield { type: 'message.start', messageId, conversationId, strategy: 'mock-replay' };
    const t0 = Date.now();
    try {
      for (const step of fixture.steps) yield* this.emit(step, signal);
      yield { type: 'message.end', messageId, finishReason: 'stop', usage: { ...fixture.usage, queries: fixture.queries, elapsedMs: fixture.usage?.latency?.totalMs ?? Date.now() - t0 } as { queries: number; elapsedMs: number } };
    } catch (e) {
      if ((e as Error).name !== 'AbortError') throw e;
      yield { type: 'message.end', messageId, finishReason: 'aborted' };
    }
  }

  async fetchBlob(id: string): Promise<Blob> {
    const b = BLOBS[id];
    if (!b) throw new Error('blob not found');
    return new Blob([b.content], { type: b.mime });
  }

  private async *emit(step: FixtureStep, signal: AbortSignal): AsyncIterable<StreamEvent> {
    if ('status' in step) {
      yield { type: 'status', label: step.status };
      await sleep(this.delayMs * 12, signal);
      return;
    }
    const part: Part = step;
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
