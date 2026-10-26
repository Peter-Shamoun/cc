import { describe, expect, it, vi } from 'vitest';
import { onceOk } from './onceOk';
import { scheduleIdlePrefetch, shouldPrefetch } from './idlePrefetch';

describe('onceOk: lazy chunk loaders', () => {
  it('memoizes success: one import however many callers', async () => {
    const load = vi.fn(async () => 'mod');
    const get = onceOk(load);
    expect(await Promise.all([get(), get(), get()])).toEqual(['mod', 'mod', 'mod']);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('forgets a failure (offline at prefetch time): the next use retries and succeeds', async () => {
    const load = vi.fn().mockRejectedValueOnce(new TypeError('Failed to fetch dynamically imported module')).mockResolvedValue('mod');
    const get = onceOk(load);
    await expect(get()).rejects.toThrow('Failed to fetch');
    expect(await get()).toBe('mod');
    expect(load).toHaveBeenCalledTimes(2);
  });
});

describe('idle prefetch of first-answer chunks', () => {
  const manualIdle = () => { const q: (() => void)[] = []; return { q, idle: (cb: () => void) => { q.push(cb); }, run: async () => { q.shift()!(); await new Promise((r) => setTimeout(r, 0)); } }; };

  it('runs one task per idle slot, in order, and keeps going past a failure', async () => {
    const order: string[] = [];
    const m = manualIdle();
    scheduleIdlePrefetch([
      async () => { order.push('md'); },
      async () => { order.push('math'); throw new Error('offline'); },
      async () => { order.push('vega'); },
    ], { idle: m.idle });
    expect(order).toEqual([]); // nothing on the critical path
    await m.run(); expect(order).toEqual(['md']);
    expect(m.q).toHaveLength(1); // the next task waits for its own idle slot
    await m.run(); await m.run();
    expect(order).toEqual(['md', 'math', 'vega']);
  });

  it('on Save-Data or 2G only the Markdown renderer is warmed (charts and math load on first use)', async () => {
    for (const conn of [{ saveData: true }, { effectiveType: '2g' }, { effectiveType: 'slow-2g' }]) {
      const ran: string[] = [];
      const m = manualIdle();
      scheduleIdlePrefetch([async () => { ran.push('md'); }, async () => { ran.push('math'); }], { idle: m.idle, conn });
      await m.run();
      expect(m.q).toHaveLength(0);
      expect(ran).toEqual(['md']);
    }
    expect(shouldPrefetch({ effectiveType: '4g' })).toBe(true);
    expect(shouldPrefetch({ effectiveType: '3g' })).toBe(true);
    expect(shouldPrefetch(undefined)).toBe(true);
  });
});
