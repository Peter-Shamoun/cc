import { afterEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render, waitFor } from '@testing-library/react';
import type { ChartPart } from '../../lib/contract';

// The first chart of a session used to compile, parse and draw in one ~400 ms task on a 6x-throttled CPU.
// ChartView now compiles Vega-Lite itself, yields a macrotask (so pending keystrokes run), then embeds the compiled Vega.
const h = vi.hoisted(() => ({ log: [] as string[], compile: null as null | ((s: unknown, o: unknown) => { spec: unknown }) }));
vi.mock('vega-embed', () => ({
  default: vi.fn(async (_el: unknown, spec: { kind?: string }, opts: Record<string, unknown>) => {
    h.log.push(`embed:${spec.kind ?? 'vl'}:${String(opts.mode)}`);
    return { view: { finalize() {}, toImageURL: async () => '' } };
  }),
  get vegaLite() { return h.compile ? { compile: h.compile } : undefined; },
}));
afterEach(() => { cleanup(); h.log = []; h.compile = null; });

const chart = { type: 'chart', id: 'c1', title: 'Monthly denial rate', alt: 'line chart', spec: { mark: 'line' }, data: { values: [{ m: 1, v: 2 }] } } as unknown as ChartPart;

async function setup() {
  const embed = (await import('vega-embed')).default as unknown as ReturnType<typeof vi.fn>;
  embed.mockClear();
  const { ChartView } = await import('./ChartView');
  return { embed, ChartView };
}

describe('chart first render is split across tasks (6x CPU)', () => {
  it('compiles with the theme config, yields a macrotask, then embeds the compiled spec exactly like embed\'s own VL path', async () => {
    const compile = vi.fn((_s: unknown, _o: unknown) => { h.log.push('compile'); queueMicrotask(() => h.log.push('microtask')); setTimeout(() => h.log.push('macrotask'), 0); return { spec: { kind: 'vega' } }; });
    h.compile = compile;
    const { embed, ChartView } = await setup();
    render(<ChartView part={chart} />);
    await waitFor(() => expect(embed).toHaveBeenCalledTimes(1));
    // Something queued as a macrotask after compiling runs before embed: the main thread got a turn in between.
    expect(h.log).toEqual(['compile', 'microtask', 'macrotask', 'embed:vega:vega']);
    const [spec, opts] = compile.mock.calls[0] as [Record<string, unknown>, { config: Record<string, unknown> }];
    expect(spec.$schema ?? spec.mark).toBeTruthy();
    expect(opts.config).toMatchObject({ background: 'transparent', view: { stroke: null } });
    expect(embed.mock.calls[0][2]).toMatchObject({ mode: 'vega', config: {}, hover: false, ast: true, actions: false, defaultStyle: false });
  });

  it('does not embed when the chart unmounts while yielding', async () => {
    let unmount = () => {};
    h.compile = () => { queueMicrotask(() => unmount()); return { spec: { kind: 'vega' } }; };
    const { embed, ChartView } = await setup();
    unmount = render(<ChartView part={chart} />).unmount;
    await new Promise((res) => setTimeout(res, 50));
    expect(embed).not.toHaveBeenCalled();
  });

  it('falls back to a single embed with the config when vega-lite is not exposed', async () => {
    const { embed, ChartView } = await setup();
    render(<ChartView part={chart} />);
    await waitFor(() => expect(embed).toHaveBeenCalledTimes(1));
    expect(h.log).toEqual(['embed:vl:undefined']);
    expect(embed.mock.calls[0][2]).toMatchObject({ config: { background: 'transparent' } });
  });
});
