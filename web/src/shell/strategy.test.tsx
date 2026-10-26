import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, cleanup, fireEvent, render, renderHook, screen, waitFor, within } from '@testing-library/react';
import App from '../App';
import { HttpAnalystAdapter } from '../adapters/http';
import { DEMO_CAPABILITIES } from '../contract/capabilities';
import { useStrategy, tierLabel } from './useStrategy';

vi.mock('../adapters/mock', async (orig) => {
  const m = await orig<typeof import('../adapters/mock')>();
  return { ...m, MockAnalystAdapter: class extends m.MockAnalystAdapter { constructor() { super(0); } } };
});
vi.mock('vega-embed', () => ({ default: async () => ({ view: { finalize() {}, toImageURL: async () => '' } }) }));
vi.mock('shiki', () => ({ createHighlighter: async () => ({ codeToHtml: (c: string) => `<pre>${c}</pre>` }) }));

afterEach(() => { cleanup(); localStorage.clear(); vi.unstubAllGlobals(); vi.restoreAllMocks(); });

describe('strategy picker (contract 1.4.0)', () => {
  it('lists the demo strategies with tiers, and a pick rides on the next request as options', async () => {
    const { MockAnalystAdapter } = await import('../adapters/mock');
    const spy = vi.spyOn(MockAnalystAdapter.prototype, 'streamTurn');
    render(<App />);
    fireEvent.click(screen.getByTestId('product-menu'));
    const menu = screen.getByRole('menu', { name: 'CC' });
    const std = within(menu).getByRole('menuitemradio', { name: /Standard/ });
    expect(std).toHaveAttribute('aria-checked', 'true');
    expect(std).toHaveTextContent('Low cost · Fast');
    await waitFor(() => expect(document.activeElement).toBe(std));
    fireEvent.click(within(menu).getByRole('menuitemradio', { name: /Cross-check/ }));
    expect(screen.queryByRole('menu')).toBeNull();
    expect(screen.getByTestId('product-menu')).toHaveAccessibleName(/Cross-check/);
    expect(localStorage.getItem('cc.pref.strategy')).toBe('candidates');
    const box = screen.getByRole('textbox', { name: 'Message' });
    fireEvent.change(box, { target: { value: 'Total paid by payer' } });
    fireEvent.keyDown(box, { key: 'Enter' });
    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0][0].options).toEqual({ strategy: 'candidates' });
  });

  it('sends no options on the defaults, and offers models only for strategies with a choice', async () => {
    const { result } = renderHook(() => useStrategy());
    expect(result.current.options()).toBeUndefined();
    expect(result.current.models.map((m) => m.id)).toEqual(['auto', 'claude-haiku-5-5', 'claude-sonnet-5-5']);
    act(() => result.current.setModel('claude-sonnet-5-5'));
    expect(result.current.options()).toEqual({ model: 'claude-sonnet-5-5' });
    act(() => result.current.setStrategy('cascade'));
    expect(result.current.models.map((m) => m.id)).toEqual(['auto']);
    expect(result.current.options()).toEqual({ strategy: 'cascade' });
  });

  it('ignores a stored pick the server no longer offers or marks unavailable', async () => {
    localStorage.setItem('cc.pref.strategy', 'candidates');
    const caps = { ...DEMO_CAPABILITIES, demo: false, strategies: DEMO_CAPABILITIES.strategies.map((s) => (s.id === 'candidates' ? { ...s, available: false } : s)) };
    const f = vi.fn(async (_u: string, _i?: RequestInit) => new Response(JSON.stringify(caps), { status: 200 }));
    vi.stubGlobal('fetch', f);
    const adapter = new HttpAnalystAdapter('', 'demo-token');
    const load = () => adapter.capabilities();
    const { result } = renderHook(() => useStrategy(load));
    await waitFor(() => expect(result.current.caps.demo).toBe(false));
    expect(result.current.strategy?.id).toBe('tool_loop');
    expect(result.current.options()).toBeUndefined();
    expect(f.mock.calls[0][0]).toBe('/api/capabilities');
    expect(new Headers(f.mock.calls[0][1]?.headers).get('authorization')).toMatch(/demo-token/);
  });

  it('the http adapter puts options in every request body, including fallbacks', async () => {
    const bodies: { options?: unknown }[] = [];
    const responses = [new Response('{"error":{"code":"validation","message":"x"}}', { status: 404 }), new Response('', { status: 500 })];
    vi.stubGlobal('fetch', vi.fn(async (_u: string, init: RequestInit) => { bodies.push(JSON.parse(init.body as string)); return responses.shift()!; }));
    const it = new HttpAnalystAdapter('').streamTurn({ conversationId: 'c', signal: new AbortController().signal, messages: [{ id: 'u', role: 'user', text: 'q' }], regenerate: { messageId: 'm' }, options: { strategy: 'cascade' } });
    for await (const _ of it) void _;
    expect(bodies.map((b) => b.options)).toEqual([{ strategy: 'cascade' }, { strategy: 'cascade' }]);
  });

  it('labels tiers in words', () => {
    expect(tierLabel('high', 'slow')).toBe('Higher cost · Slower');
  });
});
