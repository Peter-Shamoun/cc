import { ChevronDown, Copy, Download, Ellipsis, Keyboard, Moon, PanelLeft, Settings, Share, SquarePen, Sun } from 'lucide-react';
import { useTheme } from '../lib/theme';
import { IconButton } from '../components/ui';
import { Menu, useMenu, type MenuItem } from './primitives';

export interface HeaderProps {
  showNav: boolean;
  onOpenSidebar: () => void;
  onNewChat: () => void;
  adapterName: string;
  hasChat: boolean;
  onCopyMarkdown: () => void;
  onDownloadMarkdown: () => void;
  onSettings: () => void;
  onShortcuts: () => void;
}

/** Top bar: sidebar/new-chat when the sidebar is hidden, product label, scope chips, Share and "…" (ChatGPT layout). */
export function Header(p: HeaderProps) {
  const { mode, toggleMode } = useTheme();
  const share = useMenu();
  const more = useMenu();
  const shareItems: MenuItem[] = [
    { label: 'Copy as Markdown', icon: <Copy size={16} />, onSelect: p.onCopyMarkdown },
    { label: 'Download .md', icon: <Download size={16} />, onSelect: p.onDownloadMarkdown },
  ];
  const moreItems: MenuItem[] = [
    { label: mode === 'dark' ? 'Light mode' : 'Dark mode', icon: mode === 'dark' ? <Sun size={16} /> : <Moon size={16} />, onSelect: toggleMode },
    { label: 'Settings', icon: <Settings size={16} />, onSelect: p.onSettings },
    { label: 'Keyboard shortcuts', icon: <Keyboard size={16} />, onSelect: p.onShortcuts },
  ];
  return (
    <header className="flex h-[52px] shrink-0 items-center justify-between gap-2 px-2" data-testid="app-header">
      <div className="flex min-w-0 items-center gap-1">
        {p.showNav && (
          <>
            <IconButton label="Open sidebar" onClick={p.onOpenSidebar}><PanelLeft size={18} /></IconButton>
            <IconButton label="New chat" onClick={p.onNewChat} className="max-sm:hidden"><SquarePen size={18} /></IconButton>
          </>
        )}
        <button type="button" className="flex min-w-0 items-center gap-1 rounded-lg px-2.5 py-1.5 text-lg hover:bg-hover" title="Analyst: SQL over the de-identified claims lake">
          <span className="truncate">ClaimClarity <span className="text-fg-subtle max-sm:hidden">Analyst</span></span>
          <ChevronDown size={16} className="shrink-0 text-fg-subtle" />
        </button>
      </div>
      <div className="flex shrink-0 items-center gap-1.5 whitespace-nowrap">
        <span className="hidden rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted xl:inline" title="Dataset scope">payer: all · 2025-10..2026-09</span>
        <span className="hidden rounded-full bg-[#10a37f]/12 px-2.5 py-1 text-xs font-medium text-[#0b7a5f] lg:inline dark:text-[#4fd1a5]" title="PHI mode, set by the server">De-identified</span>
        {p.adapterName === 'http' && <span className="hidden rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted lg:inline" data-testid="live-badge">Live API</span>}
        {p.adapterName === 'mock' && <span className="hidden rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted lg:inline">Demo data</span>}
        {p.hasChat && (
          <button type="button" onClick={share.toggle} aria-haspopup="menu" aria-expanded={!!share.anchor} className="flex h-9 items-center gap-1.5 rounded-full border border-line px-3 text-sm hover:bg-hover max-sm:w-9 max-sm:justify-center max-sm:px-0" aria-label="Share chat">
            <Share size={15} /> <span className="max-sm:hidden">Share</span>
          </button>
        )}
        {p.showNav && (
          <IconButton label="New chat" onClick={p.onNewChat} className="sm:hidden"><SquarePen size={18} /></IconButton>
        )}
        <IconButton label="More options" aria-haspopup="menu" aria-expanded={!!more.anchor} onClick={more.toggle}><Ellipsis size={18} /></IconButton>
        {share.anchor && <Menu label="Share chat" anchor={share.anchor} items={shareItems} onClose={share.close} align="end" />}
        {more.anchor && <Menu label="More options" anchor={more.anchor} items={moreItems} onClose={more.close} align="end" />}
      </div>
    </header>
  );
}
