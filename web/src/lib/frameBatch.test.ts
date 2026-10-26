import { describe, expect, it, vi } from 'vitest';
import { frameBatch } from './frameBatch';

describe('frameBatch', () => {
  it('flushes many scheduled updates once per frame', async () => {
    const flush = vi.fn();
    const b = frameBatch(flush);
    for (let i = 0; i < 50; i++) b.schedule();
    expect(flush).not.toHaveBeenCalled();
    expect(b.pending).toBe(true);
    await new Promise((r) => setTimeout(r, 60));
    expect(flush).toHaveBeenCalledTimes(1);
    b.schedule();
    await new Promise((r) => setTimeout(r, 60));
    expect(flush).toHaveBeenCalledTimes(2);
  });

  it('now() flushes immediately and cancels the queued frame', async () => {
    const flush = vi.fn();
    const b = frameBatch(flush);
    b.schedule();
    b.now();
    expect(flush).toHaveBeenCalledTimes(1);
    expect(b.pending).toBe(false);
    await new Promise((r) => setTimeout(r, 60));
    expect(flush).toHaveBeenCalledTimes(1);
  });
});
