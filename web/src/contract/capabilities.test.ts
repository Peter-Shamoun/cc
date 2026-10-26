import fs from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';
import { CONTRACT_VERSION } from '../api/contract';
import { DEMO_CAPABILITIES, loadCapabilities } from './capabilities';

const FILE = path.resolve(process.cwd(), '../api/contract/examples/capabilities.json');

describe('capabilities (CONTRACT.md §12)', () => {
  it('DEMO_CAPABILITIES mirrors the demo backend (examples/capabilities.json)', () => {
    const served = JSON.parse(fs.readFileSync(FILE, 'utf8'));
    expect(DEMO_CAPABILITIES).toEqual({ ...served, contractVersion: CONTRACT_VERSION });
    expect(served.contractVersion).toBe(CONTRACT_VERSION);
  });
  it('offers no strategy or model choice (1.24.0)', () => {
    for (const key of ['defaults', 'strategies', 'models', 'scope']) expect(DEMO_CAPABILITIES).not.toHaveProperty(key);
  });
  it('loadCapabilities uses the server, and falls back offline or on a bad body', async () => {
    const served = { ...DEMO_CAPABILITIES, demo: false, pinArchive: false };
    const ok = (body: unknown, status = 200) => async () => new Response(JSON.stringify(body), { status });
    expect(await loadCapabilities('', ok(served) as typeof fetch)).toEqual(served);
    expect(await loadCapabilities('', ok({}, 500) as typeof fetch)).toBe(DEMO_CAPABILITIES);
    expect(await loadCapabilities('', ok({ nope: 1 }) as typeof fetch)).toBe(DEMO_CAPABILITIES);
    expect(await loadCapabilities('', (async () => { throw new TypeError('offline'); }) as typeof fetch)).toBe(DEMO_CAPABILITIES);
  });
});
