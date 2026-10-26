import { useEffect, useRef, useState } from 'react';
import { Check, ChevronDown, Copy, Database, Download, Ellipsis, Keyboard, Moon, PanelLeft, Pencil, Settings, Share, ShieldCheck, SquarePen, Sun, Trash2 } from 'lucide-react';
import { useTheme } from '../lib/theme';
import { IconButton } from '../components/ui';
import { Menu, modKey, useMenu, type MenuItem } from './primitives';
import { tierLabel, type StrategyState } from './useStrategy';

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
  /** Open chat's title (Claude-style title menu: rename, export, delete). */
  title?: string;
  onRename?: (title: string) => void;
  onDelete?: () => void;
  /** Opens the Share & export dialog (replaces the share menu when set). */
  onShare?: () => void;
  /** Strategy/model picker (contract 1.4.0) in the product menu, like ChatGPT's model menu. */
  strategy?: StrategyState;
}

/** Top bar: sidebar/new-chat when the sidebar is hidden, product menu (scope + data mode), PHI badge, Share and "…" (ChatGPT layout). */
export function Header(p: HeaderProps) {
  const { mode, toggleMode } = useTheme();
  const share = useMenu();
  const more = useMenu();
  const product = useMenu();
  const titleMenu = useMenu();
  const [renaming, setRenaming] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const titleBtn = useRef<HTMLButtonElement>(null);
  useEffect(() => { if (renaming) input.current?.select(); }, [renaming]);
  const endRename = (save: boolean) => {
    const v = input.current?.value.trim();
    if (save && v && v !== p.title) p.onRename?.(v);
    setRenaming(false);
    requestAnimationFrame(() => titleBtn.current?.focus());
  };
  const titleItems: MenuItem[] = [
    { label: 'Rename', icon: <Pencil size={16} />, onSelect: () => setRenaming(true) },
    { label: 'Export as Markdown', icon: <Download size={16} />, onSelect: p.onDownloadMarkdown },
    ...(p.onDelete ? ['sep' as const, { label: 'Delete', icon: <Trash2 size={16} />, danger: true, onSelect: p.onDelete }] : []),
  ];
  const dataLabel = p.adapterName === 'http' ? 'Live API' : 'Demo data';
  // Scope + data mode live in the product menu (QA-S6): ChatGPT/Claude headers stay near-empty.
  const st = p.strategy;
  const productItems: MenuItem[] = st?.strategy ? [
    { heading: 'Strategy' },
    ...st.caps.strategies.map((s): MenuItem => ({ label: s.label, description: s.description, meta: tierLabel(s.costTier, s.latencyTier), checked: s.id === st.strategy?.id, disabled: s.available === false, title: s.unavailableReason, onSelect: () => st.setStrategy(s.id) })),
    ...(st.models.length > 1 ? ['sep' as const, { heading: 'Model' }, ...st.models.map((m): MenuItem => ({ label: m.label, description: m.description, checked: m.id === st.model?.id, disabled: m.available === false, title: m.unavailableReason, onSelect: () => st.setModel(m.id) }))] : []),
    'sep',
    { label: `Data: ${dataLabel}${st.caps.demo ? ' · scripted runs' : ''}`, icon: <span className="block h-2 w-2 rounded-full bg-[#10a37f]" />, onSelect: () => {} },
  ] : [
    { label: 'Analyst · SQL over the de-identified claims lake', icon: <Check size={16} />, onSelect: () => {} },
    'sep',
    { label: 'Scope: payer all · 2025-10..2026-09', icon: <Database size={16} />, onSelect: () => {} },
    { label: `Data: ${dataLabel}`, icon: <span className="block h-2 w-2 rounded-full bg-[#10a37f]" />, onSelect: () => {} },
  ];
  // The picked strategy shows in the button only when it is not the default (ChatGPT shows the model name).
  const pickLabel = st && !st.isDefault ? [st.strategy?.label, st.model && st.model.id !== st.caps.defaults.model ? st.model.label : ''].filter(Boolean).join(' · ') : '';
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
            <IconButton label="Open sidebar" tip={`Open sidebar · ${modKey()} ⇧ S`} onClick={p.onOpenSidebar}><PanelLeft size={18} /></IconButton>
            <IconButton label="New chat" tip={`New chat · ${modKey()} ⇧ O`} onClick={p.onNewChat} className="max-sm:hidden"><SquarePen size={18} /></IconButton>
          </>
        )}
        <button type="button" onClick={product.toggle} aria-haspopup="menu" aria-expanded={!!product.anchor} data-testid="product-menu" data-mode={p.adapterName}
          className="flex shrink-0 items-center gap-1 rounded-lg px-2.5 py-1.5 text-lg hover:bg-hover" aria-label={`CC Analyst, ${pickLabel ? `${pickLabel}, ` : ''}${dataLabel}`}>
          <span className="whitespace-nowrap">CC <span className={p.hasChat && p.title ? 'text-fg-subtle @max-[820px]:hidden' : 'text-fg-subtle @max-[560px]:hidden'}>{pickLabel || 'Analyst'}</span></span>
          <ChevronDown size={16} className="shrink-0 text-fg-subtle" />
        </button>
        {p.hasChat && p.title && (
          <div className="flex min-w-0 items-center @max-[520px]:hidden" data-testid="header-title">
            <span className="mx-0.5 text-fg-subtle/60" aria-hidden>/</span>
            {renaming ? (
              <input ref={input} defaultValue={p.title} aria-label="Rename chat" maxLength={120}
                className="w-[min(28rem,40cqw)] min-w-0 rounded-md border border-[var(--focus)] bg-bg px-2 py-1 text-sm outline-none"
                onBlur={() => endRename(true)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); endRename(true); } if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); endRename(false); } }} />
            ) : (
              <button ref={titleBtn} type="button" onClick={titleMenu.toggle} onDoubleClick={() => setRenaming(true)} aria-haspopup="menu" aria-expanded={!!titleMenu.anchor}
                aria-label={`Chat options: ${p.title}`} title={p.title}
                className="flex min-w-0 max-w-[min(28rem,40cqw)] items-center gap-1 rounded-lg px-2 py-1.5 text-sm text-fg-muted hover:bg-hover hover:text-fg">
                <span className="truncate">{p.title}</span>
                <ChevronDown size={14} className="shrink-0 text-fg-subtle" />
              </button>
            )}
            {titleMenu.anchor && <Menu label="Chat options" anchor={titleMenu.anchor} items={titleItems} onClose={titleMenu.close} />}
          </div>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1.5 whitespace-nowrap">
        <span className="hidden items-center gap-1 px-1.5 text-xs text-fg-subtle @[440px]:inline-flex" title="PHI mode, set by the server" data-testid="phi-badge"><ShieldCheck size={13} strokeWidth={1.75} aria-hidden /> De-identified</span>
        {p.hasChat && (
          <button type="button" onClick={p.onShare ?? share.toggle} aria-haspopup={p.onShare ? 'dialog' : 'menu'} aria-expanded={p.onShare ? undefined : !!share.anchor} data-testid="share-button" className="flex h-9 items-center gap-1.5 rounded-full border border-line px-3 text-sm hover:bg-hover @max-[600px]:w-9 @max-[600px]:justify-center @max-[600px]:px-0" aria-label="Share chat">
            <Share size={15} /> <span className="@max-[600px]:hidden">Share</span>
          </button>
        )}
        {p.showNav && (
          <IconButton label="New chat" onClick={p.onNewChat} className="sm:hidden"><SquarePen size={18} /></IconButton>
        )}
        <IconButton label="More options" tip tipAt="end" aria-haspopup="menu" aria-expanded={!!more.anchor} onClick={more.toggle}><Ellipsis size={18} /></IconButton>
        {share.anchor && <Menu label="Share chat" anchor={share.anchor} items={shareItems} onClose={share.close} align="end" />}
        {product.anchor && <Menu label="CC" anchor={product.anchor} items={productItems} onClose={product.close} className={st ? 'w-[min(24rem,calc(100vw-16px))]' : undefined} />}
        {more.anchor && <Menu label="More options" anchor={more.anchor} items={moreItems} onClose={more.close} align="end" />}
      </div>
    </header>
  );
}
