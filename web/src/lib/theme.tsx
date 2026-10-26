import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { getPref, onPrefChange, setPref } from './prefs';

/** Resolved colour mode actually painted. */
export type Mode = 'light' | 'dark';
/** What the user picked in Settings: ChatGPT/Claude both default to following the OS. */
export type ModePref = Mode | 'system';
/** neutral = ChatGPT-like (default), paper = Claude-like (spec §1.3, §2). */
export type Palette = 'neutral' | 'paper';

interface ThemeCtx { mode: Mode; preference: ModePref; palette: Palette; toggleMode: () => void; setPreference: (m: ModePref) => void; setPalette: (p: Palette) => void }
const Ctx = createContext<ThemeCtx>({ mode: 'light', preference: 'system', palette: 'neutral', toggleMode: () => {}, setPreference: () => {}, setPalette: () => {} });

const darkQuery = () => (typeof window !== 'undefined' && typeof window.matchMedia === 'function' ? window.matchMedia('(prefers-color-scheme: dark)') : null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreference] = useState<ModePref>(() => {
    const p = getPref('theme');
    return p === 'light' || p === 'dark' || p === 'system' ? p : 'system';
  });
  const [osDark, setOsDark] = useState(() => darkQuery()?.matches ?? false);
  useEffect(() => {
    const mq = darkQuery();
    if (!mq) return;
    const on = () => setOsDark(mq.matches);
    mq.addEventListener?.('change', on);
    return () => mq.removeEventListener?.('change', on);
  }, []);
  const [palette, setPaletteState] = useState<Palette>(() => { const p = getPref('palette'); return p === 'paper' || p === 'neutral' ? p : 'neutral'; });
  // Theme or palette changed in another tab: follow it, like ChatGPT/Claude do. Invalid values are ignored.
  useEffect(() => {
    const offTheme = onPrefChange('theme', (v) => { if (v === 'light' || v === 'dark' || v === 'system') setPreference(v); });
    const offPalette = onPrefChange('palette', (v) => { if (v === 'neutral' || v === 'paper') setPaletteState(v); });
    return () => { offTheme(); offPalette(); };
  }, []);
  const mode: Mode = preference === 'system' ? (osDark ? 'dark' : 'light') : preference;
  useEffect(() => {
    const el = document.documentElement;
    // Suppress colour transitions for the one frame the theme flips, so nothing "fades" between themes.
    el.classList.add('theme-switching');
    el.classList.toggle('dark', mode === 'dark');
    el.style.colorScheme = mode;
    el.dataset.palette = palette;
    const id = window.setTimeout(() => el.classList.remove('theme-switching'), 50);
    setPref('theme', preference);
    setPref('palette', palette);
    return () => window.clearTimeout(id);
  }, [mode, palette, preference]);
  const toggleMode = useCallback(() => setPreference(mode === 'dark' ? 'light' : 'dark'), [mode]);
  return <Ctx.Provider value={{ mode, preference, palette, toggleMode, setPreference, setPalette: setPaletteState }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);

/** Chart palettes from spec §2.3. */
export const CHART_COLORS: Record<Palette, string[]> = {
  // Restrained: one muted blue leads; the rest are desaturated, similar in weight, so no series shouts (Peter 10:06 PM: no neon/rainbow).
  neutral: ['#5B7FA6', '#C08552', '#6F9A7A', '#B0625C', '#7EA6A6', '#B5A05F', '#8C7FA0', '#8A7A6B'],
  paper: ['#D97757', '#6A9BCC', '#788C5D', '#C46686', '#B8A16B', '#5E5D59', '#3898EC', '#B53333'],
};
