import { describe, expect, it } from 'vitest';
import lock from '../../package-lock.json';
import pkg from '../../package.json';
import { audit, classify } from '../../scripts/license_audit.mjs';
import notices from '../../THIRD_PARTY_NOTICES.md?raw';

describe('licence audit (docs/frontend_licenses.md)', () => {
  it('classifies SPDX expressions', () => {
    expect(classify('MIT')).toBe('permissive');
    expect(classify('(MIT OR GPL-3.0)')).toBe('permissive');
    expect(classify('Apache-2.0 AND MPL-2.0')).toBe('weak-copyleft');
    expect(classify('GPL-3.0-only')).toBe('copyleft-or-restricted');
    expect(classify('AGPL-3.0')).toBe('copyleft-or-restricted');
    expect(classify('')).toBe('unknown');
    expect(classify('SEE LICENSE IN LICENSE.txt')).toBe('unknown');
  });

  it('every package that ships in the bundle is permissively licensed', () => {
    const { problems } = audit(lock as never);
    expect(problems).toEqual([]);
  });

  it('non-permissive licences appear only in dev/build tooling, and only the reviewed ones', () => {
    const { rows } = audit(lock as never);
    const devFlagged = new Set(rows.filter((r) => r.dev && r.cls !== 'permissive').map((r) => r.name.replace(/-(android|darwin|freebsd|linux|win32)-.*$/, '')));
    // Reviewed in docs/frontend_licenses.md: MPL-2.0 build/test tools, used unmodified, never bundled.
    expect([...devFlagged].sort()).toEqual(['@axe-core/playwright', 'axe-core', 'lightningcss']);
  });

  it('every direct runtime dependency is listed in THIRD_PARTY_NOTICES.md', () => {
    for (const name of Object.keys((pkg as { dependencies: Record<string, string> }).dependencies)) expect(notices, name).toContain(name);
  });
});
