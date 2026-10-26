import { ChevronDown, Moon, PanelLeft, Sun, SquarePen, Palette as PaletteIcon } from 'lucide-react';
import { useTheme } from '../lib/theme';
import { IconButton } from '../components/ui';

/** Top bar of the main column: sidebar/new-chat when the sidebar is hidden, model label, scope chips, theme toggles. */
export function Header({ showNav, onOpenSidebar, onNewChat, adapterName }: { showNav: boolean; onOpenSidebar: () => void; onNewChat: () => void; adapterName: string }) {
  const { mode, palette, toggleMode, setPalette } = useTheme();
  return (
    <header className="flex h-[52px] shrink-0 items-center justify-between px-2">
      <div className="flex items-center gap-1">
        {showNav && (
          <>
            <IconButton label="Open sidebar" onClick={onOpenSidebar}><PanelLeft size={18} /></IconButton>
            <IconButton label="New chat" onClick={onNewChat}><SquarePen size={18} /></IconButton>
          </>
        )}
        <button type="button" className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-lg hover:bg-hover">
          CC <span className="text-fg-subtle">Analyst</span>
          <ChevronDown size={16} className="text-fg-subtle" />
        </button>
      </div>
      <div className="flex items-center gap-1.5 whitespace-nowrap">
        <span className="hidden rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted xl:inline" title="Dataset scope">payer: all · 2025-10..2026-09</span>
        <span className="hidden rounded-full bg-[#10a37f]/12 sm:inline px-2.5 py-1 text-xs font-medium text-[#0b7a5f] dark:text-[#4fd1a5]" title="PHI mode, set by the server">De-identified</span>
        {adapterName === 'http' && <span className="hidden rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted sm:inline" data-testid="live-badge">Live API</span>}
        {adapterName === 'mock' && <span className="hidden rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted sm:inline">Demo data</span>}
        <IconButton label={palette === 'paper' ? 'Neutral theme' : 'Paper theme'} onClick={() => setPalette(palette === 'paper' ? 'neutral' : 'paper')}><PaletteIcon size={18} /></IconButton>
        <IconButton label={mode === 'dark' ? 'Light mode' : 'Dark mode'} onClick={toggleMode}>{mode === 'dark' ? <Sun size={18} /> : <Moon size={18} />}</IconButton>
      </div>
    </header>
  );
}
