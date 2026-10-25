import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from 'react';
import { getPref, setPref } from './prefs';

export type Mode = 'light' | 'dark';
/** neutral = ChatGPT-like (default), paper = Claude-like (spec §1.3, §2). */
export type Palette = 'neutral' | 'paper';

interface ThemeCtx { mode: Mode; palette: Palette; toggleMode: () => void; setPalette: (p: Palette) => void }
const Ctx = createContext<ThemeCtx>({ mode: 'light', palette: 'neutral', toggleMode: () => {}, setPalette: () => {} });

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<Mode>(() => (getPref('theme') as Mode) ?? (typeof matchMedia !== 'undefined' && matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'));
  const [palette, setPaletteState] = useState<Palette>(() => (getPref('palette') as Palette) ?? 'neutral');
  useEffect(() => {
    const el = document.documentElement;
    el.classList.toggle('dark', mode === 'dark');
    el.dataset.palette = palette;
    setPref('theme', mode);
    setPref('palette', palette);
  }, [mode, palette]);
  const toggleMode = useCallback(() => setMode((m) => (m === 'dark' ? 'light' : 'dark')), []);
  return <Ctx.Provider value={{ mode, palette, toggleMode, setPalette: setPaletteState }}>{children}</Ctx.Provider>;
}

export const useTheme = () => useContext(Ctx);

/** Chart palettes from spec §2.3. */
export const CHART_COLORS: Record<Palette, string[]> = {
  neutral: ['#4C78A8', '#F58518', '#54A24B', '#E45756', '#72B7B2', '#EECA3B', '#B279A2', '#9D755D'],
  paper: ['#D97757', '#6A9BCC', '#788C5D', '#C46686', '#B8A16B', '#5E5D59', '#3898EC', '#B53333'],
};
