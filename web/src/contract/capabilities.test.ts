import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { CONTRACT_VERSION } from '../api/contract';
import { DEMO_CAPABILITIES, defaultStrategy, loadCapabilities, modelsFor, runOptions } from './capabilities';

const FILE = path.resolve(process.cwd(), '../api/contract/examples/capabilities.json');

describe('capabilities (contract 1.4.0)', () => {
  it('DEMO_CAPABILITIES mirrors the demo backend (examples/capabilities.json)', () => {
    const served = JSON.parse(fs.readFileSync(FILE, 'utf8'));
    expect(DEMO_CAPABILITIES).toEqual({ ...served, contractVersion: CONTRACT_VERSION });
    expect(served.contractVersion).toBe(CONTRACT_VERSION);
  });
  it('exactly one default strategy and model, and defaults point at them', () => {
    expect(DEMO_CAPABILITIES.strategies.filter((s) => s.default)).toHaveLength(1);
    expect(DEMO_CAPABILITIES.models.filter((m) => m.default)).toHaveLength(1);
    expect(defaultStrategy(DEMO_CAPABILITIES)?.id).toBe(DEMO_CAPABILITIES.defaults.strategy);
  });
  it('modelsFor and runOptions', () => {
    expect(modelsFor(DEMO_CAPABILITIES, 'cascade').map((m) => m.id)).toEqual(['auto']);
    expect(modelsFor(DEMO_CAPABILITIES, 'nope')).toEqual([]);
    expect(runOptions(DEMO_CAPABILITIES, 'tool_loop', 'auto')).toEqual({});
    expect(runOptions(DEMO_CAPABILITIES, 'candidates', 'claude-sonnet-5-5')).toEqual({ strategy: 'candidates', model: 'claude-sonnet-5-5' });
  });
  it('loadCapabilities uses the server, and falls back offline or on a bad body', async () => {
    const served = { ...DEMO_CAPABILITIES, strategies: DEMO_CAPABILITIES.strategies.slice(0, 1) };
    const ok = (body: unknown, status = 200) => async () => new Response(JSON.stringify(body), { status });
    expect(await loadCapabilities('', ok(served) as typeof fetch)).toEqual(served);
    expect(await loadCapabilities('', ok({}, 500) as typeof fetch)).toBe(DEMO_CAPABILITIES);
    expect(await loadCapabilities('', ok({ nope: 1 }) as typeof fetch)).toBe(DEMO_CAPABILITIES);
    expect(await loadCapabilities('', (async () => { throw new TypeError('offline'); }) as typeof fetch)).toBe(DEMO_CAPABILITIES);
  });
});
