import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { getPref, setPref } from './prefs';

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
  const [palette, setPaletteState] = useState<Palette>(() => (getPref('palette') as Palette) ?? 'neutral');
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
  neutral: ['#4C78A8', '#F58518', '#54A24B', '#E45756', '#72B7B2', '#EECA3B', '#B279A2', '#9D755D'],
  paper: ['#D97757', '#6A9BCC', '#788C5D', '#C46686', '#B8A16B', '#5E5D59', '#3898EC', '#B53333'],
};
