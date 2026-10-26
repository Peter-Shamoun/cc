import { Check, ChevronDown, Copy, Database, Download, Ellipsis, Keyboard, Moon, PanelLeft, Settings, Share, SquarePen, Sun } from 'lucide-react';
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

/** Top bar: sidebar/new-chat when the sidebar is hidden, product menu (scope + data mode), PHI badge, Share and "…" (ChatGPT layout). */
export function Header(p: HeaderProps) {
  const { mode, toggleMode } = useTheme();
  const share = useMenu();
  const more = useMenu();
  const product = useMenu();
  const dataLabel = p.adapterName === 'http' ? 'Live API' : 'Demo data';
  // Scope + data mode live in the product menu (QA-S6): ChatGPT/Claude headers stay near-empty.
  const productItems: MenuItem[] = [
    { label: 'Analyst · SQL over the de-identified claims lake', icon: <Check size={16} />, onSelect: () => {} },
    'sep',
    { label: 'Scope: payer all · 2025-10..2026-09', icon: <Database size={16} />, onSelect: () => {} },
    { label: `Data: ${dataLabel}`, icon: <span className="block h-2 w-2 rounded-full bg-[#10a37f]" />, onSelect: () => {} },
  ];
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
    <header className="@container flex h-[52px] shrink-0 items-center justify-between gap-2 px-2" data-testid="app-header">
      <div className="flex min-w-0 items-center gap-1">
        {p.showNav && (
          <>
            <IconButton label="Open sidebar" onClick={p.onOpenSidebar}><PanelLeft size={18} /></IconButton>
            <IconButton label="New chat" onClick={p.onNewChat} className="max-sm:hidden"><SquarePen size={18} /></IconButton>
          </>
        )}
        <button type="button" onClick={product.toggle} aria-haspopup="menu" aria-expanded={!!product.anchor} data-testid="product-menu" data-mode={p.adapterName}
          className="flex min-w-0 items-center gap-1 rounded-lg px-2.5 py-1.5 text-lg hover:bg-hover" aria-label={`CC Analyst, ${dataLabel}`}>
          <span className="truncate">CC <span className="text-fg-subtle @max-[560px]:hidden">Analyst</span></span>
          <ChevronDown size={16} className="shrink-0 text-fg-subtle" />
        </button>
      </div>
      <div className="flex shrink-0 items-center gap-1.5 whitespace-nowrap">
        <span className="hidden rounded-full bg-[#10a37f]/12 px-2.5 py-1 text-xs font-medium text-[#0b7a5f] @[440px]:inline dark:text-[#4fd1a5]" title="PHI mode, set by the server" data-testid="phi-badge">De-identified</span>
        {p.hasChat && (
          <button type="button" onClick={share.toggle} aria-haspopup="menu" aria-expanded={!!share.anchor} className="flex h-9 items-center gap-1.5 rounded-full border border-line px-3 text-sm hover:bg-hover @max-[600px]:w-9 @max-[600px]:justify-center @max-[600px]:px-0" aria-label="Share chat">
            <Share size={15} /> <span className="@max-[600px]:hidden">Share</span>
          </button>
        )}
        {p.showNav && (
          <IconButton label="New chat" onClick={p.onNewChat} className="sm:hidden"><SquarePen size={18} /></IconButton>
        )}
        <IconButton label="More options" aria-haspopup="menu" aria-expanded={!!more.anchor} onClick={more.toggle}><Ellipsis size={18} /></IconButton>
        {share.anchor && <Menu label="Share chat" anchor={share.anchor} items={shareItems} onClose={share.close} align="end" />}
        {product.anchor && <Menu label="CC" anchor={product.anchor} items={productItems} onClose={product.close} />}
        {more.anchor && <Menu label="More options" anchor={more.anchor} items={moreItems} onClose={more.close} align="end" />}
      </div>
    </header>
  );
}
