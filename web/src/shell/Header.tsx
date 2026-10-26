import { useEffect, useRef, useState } from 'react';
import { ChevronDown, Copy, Download, Ellipsis, Keyboard, MessageCircleDashed, Moon, PanelLeft, Pencil, Settings, ShieldCheck, SquarePen, Sun, Trash2 } from 'lucide-react';
import { useTheme } from '../lib/theme';
import { IconButton } from '../components/ui';
import { Menu, modKey, useMenu, type MenuItem } from './primitives';

export interface HeaderProps {
  showNav: boolean;
  onOpenSidebar: () => void;
  onNewChat: () => void;
  hasChat: boolean;
  onCopyMarkdown: () => void;
  onDownloadMarkdown: () => void;
  onSettings: () => void;
  onShortcuts: () => void;
  /** Open chat's title (Claude-style title menu: rename, export, delete). */
  title?: string;
  onRename?: (title: string) => void;
  onDelete?: () => void;
  /** Server JSON export (live API only). */
  onDownloadJson?: () => void;
  /** Temporary chat toggle (QA-S22, ChatGPT-style): shown only when set. */
  temporary?: boolean;
  onTemporary?: (on: boolean) => void;
}

/** Top bar: sidebar/new-chat when the sidebar is hidden, product name, PHI badge and "…". No sharing (Peter 12:15 AM): copy/export live in the title menu. */
export function Header(p: HeaderProps) {
  const { mode, toggleMode } = useTheme();
  const more = useMenu();
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
    { label: 'Copy as Markdown', icon: <Copy size={16} />, onSelect: p.onCopyMarkdown },
    { label: 'Export as Markdown', icon: <Download size={16} />, onSelect: p.onDownloadMarkdown },
    ...(p.onDownloadJson ? [{ label: 'Export as JSON', icon: <Download size={16} />, onSelect: p.onDownloadJson }] : []),
    ...(p.onDelete ? ['sep' as const, { label: 'Delete', icon: <Trash2 size={16} />, danger: true, onSelect: p.onDelete }] : []),
  ];
  // The product name is plain text: there is no strategy/model choice (contract 1.24.0), and no data-source, scope or
  // data-mode entries either: the deployment and session decide the data server-side (CONTRACT §2.1, BACKEND-FIT).
  const productName = <span className="whitespace-nowrap">CC <span className={p.hasChat && p.title ? 'text-fg-subtle @max-[820px]:hidden' : 'text-fg-subtle @max-[560px]:hidden'}>Analyst</span></span>;
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
        <span className="shrink-0 px-2.5 py-1.5 text-lg" data-testid="product-name">{productName}</span>
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
        {p.onTemporary && (
          <IconButton label={p.temporary ? 'Turn off temporary chat' : 'Turn on temporary chat'} tip tipAt="end" aria-pressed={!!p.temporary} data-testid="temporary-toggle"
            onClick={() => p.onTemporary?.(!p.temporary)} className={p.temporary ? 'bg-hover text-fg' : undefined}>
            <MessageCircleDashed size={18} />
          </IconButton>
        )}
        {p.showNav && (
          <IconButton label="New chat" onClick={p.onNewChat} className="sm:hidden"><SquarePen size={18} /></IconButton>
        )}
        <IconButton label="More options" tip tipAt="end" aria-haspopup="menu" aria-expanded={!!more.anchor} onClick={more.toggle}><Ellipsis size={18} /></IconButton>
        {more.anchor && <Menu label="More options" anchor={more.anchor} items={moreItems} onClose={more.close} align="end" />}
      </div>
    </header>
  );
}
