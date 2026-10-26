import { describe, expect, it } from 'vitest';
import { getPref, setPref } from './prefs';

describe('prefs allowlist (spec §6.2)', () => {
  it('allows UI prefs', () => {
    setPref('theme', 'dark');
    expect(getPref('theme')).toBe('dark');
  });
  it('rejects non-allowlisted keys and long values', () => {
    expect(() => setPref('rows' as never, 'x')).toThrow();
    expect(() => setPref('theme', 'x'.repeat(100))).toThrow();
  });
});
