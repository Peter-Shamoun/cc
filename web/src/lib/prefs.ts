/**
 * The ONLY module allowed to touch localStorage (spec §6.2). Keys are
 * allowlisted UI preferences; conversation data can never be written here.
 */
export const PREF_KEYS = ['theme', 'palette', 'sidebarCollapsed', 'threadWidth', 'artifactWidth', 'enterToSend'] as const;
export type PrefKey = (typeof PREF_KEYS)[number];
const PREFIX = 'cc.pref.';

export function getPref(key: PrefKey): string | null {
  if (!PREF_KEYS.includes(key)) throw new Error(`pref key not allowed: ${key}`);
  try {
    return localStorage.getItem(PREFIX + key);
  } catch {
    return null;
  }
}

export function setPref(key: PrefKey, value: string): void {
  if (!PREF_KEYS.includes(key)) throw new Error(`pref key not allowed: ${key}`);
  if (value.length > 32) throw new Error('pref values are short UI flags only');
  try {
    localStorage.setItem(PREFIX + key, value);
  } catch {
    /* storage disabled: fine */
  }
}
