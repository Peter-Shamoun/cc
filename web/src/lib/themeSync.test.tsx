/** Theme and palette follow a change made in another tab (storage event), and ignore junk values. */
import { act, render } from '@testing-library/react';
import { afterEach, describe, expect, it } from 'vitest';
import { ThemeProvider } from './theme';
import { onPrefChange } from './prefs';

const fromOtherTab = (key: string | null, newValue: string | null) =>
  act(() => { window.dispatchEvent(new StorageEvent('storage', { key, newValue, storageArea: localStorage })); });

afterEach(() => { localStorage.clear(); document.documentElement.className = ''; delete document.documentElement.dataset.palette; });

describe('theme sync across tabs', () => {
  it('follows theme and palette changes from another tab', () => {
    localStorage.setItem('cc.pref.theme', 'light');
    render(<ThemeProvider><p>x</p></ThemeProvider>);
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    fromOtherTab('cc.pref.theme', 'dark');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    fromOtherTab('cc.pref.palette', 'paper');
    expect(document.documentElement.dataset.palette).toBe('paper');
  });

  it('ignores junk values and unrelated keys', () => {
    localStorage.setItem('cc.pref.theme', 'dark');
    render(<ThemeProvider><p>x</p></ThemeProvider>);
    fromOtherTab('cc.pref.theme', '<script>');
    fromOtherTab('cc.pref.palette', 'neon');
    fromOtherTab('other.key', 'light');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(document.documentElement.dataset.palette).toBe('neutral');
  });

  it('a stored junk palette falls back to neutral on load', () => {
    localStorage.setItem('cc.pref.palette', 'neon');
    render(<ThemeProvider><p>x</p></ThemeProvider>);
    expect(document.documentElement.dataset.palette).toBe('neutral');
  });

  it('onPrefChange only accepts allowlisted keys and reports storage clears as null', () => {
    expect(() => onPrefChange('nope' as never, () => {})).toThrow();
    const seen: (string | null)[] = [];
    const off = onPrefChange('theme', (v) => seen.push(v));
    window.dispatchEvent(new StorageEvent('storage', { key: null, newValue: null, storageArea: localStorage }));
    off();
    window.dispatchEvent(new StorageEvent('storage', { key: 'cc.pref.theme', newValue: 'dark', storageArea: localStorage }));
    expect(seen).toEqual([null]);
  });
});
