import { useEffect, useMemo, useRef, useState } from 'react';
import { ChevronDown, Moon, PanelLeft, Sun, SquarePen, ArrowDown, Palette as PaletteIcon } from 'lucide-react';
import { ThemeProvider, useTheme } from './lib/theme';
import { useChat } from './lib/useChat';
import { MockAnalystAdapter } from './adapters/mock';
import { MemoryTranscriptStore } from './adapters/memoryStore';
import { HttpAnalystAdapter } from './adapters/http';
import { AdapterCtx } from './adapters/context';
import type { AnalystAdapter } from './adapters/types';
import { Sidebar } from './components/Sidebar';
import { Composer } from './components/Composer';
import { AssistantTurn, UserBubble, type Artifact } from './components/Message';
import { ArtifactPanel } from './components/ArtifactPanel';
import { IconButton } from './components/ui';
import { SUGGESTIONS } from './fixtures';

function makeAdapter(): AnalystAdapter {
  const base = import.meta.env.VITE_ANALYST_BASE as string | undefined;
  return base !== undefined && base !== '' ? new HttpAnalystAdapter(base) : new MockAnalystAdapter();
}

function Shell() {
  const adapter = useMemo(makeAdapter, []);
  const store = useMemo(() => new MemoryTranscriptStore(), []);
  const chat = useChat(adapter, store);
  const { mode, palette, toggleMode, setPalette } = useTheme();
  const [sidebar, setSidebar] = useState(() => window.innerWidth >= 768);
  const [artifact, setArtifact] = useState<Artifact | null>(null);
  useEffect(() => { if (artifact && window.innerWidth < 1400) setSidebar(false); }, [artifact]);
  const [draft, setDraft] = useState<{ text: string; n: number } | undefined>();
  const scroller = useRef<HTMLDivElement>(null);
  const [atBottom, setAtBottom] = useState(true);
  const { messages } = chat;

  useEffect(() => {
    const el = scroller.current;
    if (el && atBottom) el.scrollTop = el.scrollHeight;
  }, [messages, atBottom]);
  useEffect(() => setArtifact(null), [chat.convId]);

  const edit = (t: string) => setDraft((d) => ({ text: t, n: (d?.n ?? 0) + 1 }));
  const empty = messages.length === 0;
  const composer = (autoFocus: boolean, placeholder?: string) => (
    <Composer onSend={(t) => { setAtBottom(true); chat.send(t); }} onStop={chat.stop} busy={chat.busy} autoFocus={autoFocus} initial={draft} placeholder={placeholder} />
  );

  return (
    <AdapterCtx.Provider value={adapter}>
      <div className="flex h-full overflow-hidden">
        {sidebar && (
          <Sidebar threads={chat.threads} activeId={chat.convId} onSelect={chat.open} onNew={chat.newChat} onDelete={chat.remove} onCollapse={() => setSidebar(false)} storeMode={store.mode} />
        )}
        <main className="relative flex min-w-[400px] flex-1 flex-col font-sans">
          <header className="flex h-[52px] shrink-0 items-center justify-between px-2">
            <div className="flex items-center gap-1">
              {!sidebar && (
                <>
                  <IconButton label="Open sidebar" onClick={() => setSidebar(true)}><PanelLeft size={18} /></IconButton>
                  <IconButton label="New chat" onClick={chat.newChat}><SquarePen size={18} /></IconButton>
                </>
              )}
              <button type="button" className="flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-lg hover:bg-hover">
                ClaimClarity <span className="text-fg-subtle">Analyst</span>
                <ChevronDown size={16} className="text-fg-subtle" />
              </button>
            </div>
            <div className="flex items-center gap-1.5 whitespace-nowrap">
              <span className="hidden rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted xl:inline" title="Dataset scope">payer: all · 2025-10..2026-09</span>
              <span className="rounded-full bg-[#10a37f]/12 px-2.5 py-1 text-xs font-medium text-[#0b7a5f] dark:text-[#4fd1a5]" title="PHI mode, set by the server">De-identified</span>
              {adapter.name === 'mock' && <span className="hidden rounded-full border border-line px-2.5 py-1 text-xs text-fg-muted sm:inline">Demo data</span>}
              <IconButton label={palette === 'paper' ? 'Neutral theme' : 'Paper theme'} onClick={() => setPalette(palette === 'paper' ? 'neutral' : 'paper')}><PaletteIcon size={18} /></IconButton>
              <IconButton label={mode === 'dark' ? 'Light mode' : 'Dark mode'} onClick={toggleMode}>{mode === 'dark' ? <Sun size={18} /> : <Moon size={18} />}</IconButton>
            </div>
          </header>

          {empty ? (
            <div className="flex flex-1 flex-col items-center justify-center px-4 pb-[12vh]">
              <h1 className="mb-7 text-center tracking-tight" style={{ fontFamily: 'var(--heading-font)', fontSize: 'var(--empty-size)', fontWeight: 'var(--empty-weight)' as never }}>What should we look into?</h1>
              <div className="w-full max-w-[48rem]">
                {composer(true)}
                <div className="mt-4 flex flex-wrap justify-center gap-2">
                  {SUGGESTIONS.map((s) => (
                    <button key={s.prompt} type="button" onClick={() => chat.send(s.prompt)} className="rounded-full border border-line bg-bg px-3.5 py-2 text-sm font-medium text-fg-muted hover:bg-hover hover:text-fg">{s.title}</button>
                  ))}
                </div>
              </div>
            </div>
          ) : (
            <>
              <div ref={scroller} onScroll={(e) => { const el = e.currentTarget; setAtBottom(el.scrollHeight - el.scrollTop - el.clientHeight < 80); }} className="flex-1 overflow-y-auto">
                <div className="mx-auto flex max-w-[48rem] flex-col gap-6 px-4 pb-10 pt-4 md:px-6">
                  {messages.map((m, i) =>
                    m.role === 'user' ? (
                      <UserBubble key={m.id} msg={m} onEdit={edit} />
                    ) : (
                      <AssistantTurn key={m.id} msg={m} isLast={i === messages.length - 1} onOpen={setArtifact} onRegenerate={chat.regenerate} onEdit={edit} />
                    ),
                  )}
                </div>
              </div>
              {!atBottom && (
                <button type="button" aria-label="Jump to latest" onClick={() => scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: 'smooth' })} className="absolute bottom-32 left-1/2 flex h-8 w-8 -translate-x-1/2 items-center justify-center rounded-full border border-line bg-bg shadow">
                  <ArrowDown size={16} />
                </button>
              )}
              <div className="mx-auto w-full max-w-[48rem] px-4 md:px-6">
                {composer(false, 'Ask a follow-up')}
                <p className="py-2 text-center text-xs text-fg-subtle">ClaimClarity can make mistakes. Check figures against source before use.</p>
              </div>
            </>
          )}
        </main>
        {artifact && <ArtifactPanel artifact={artifact} onClose={() => setArtifact(null)} />}
      </div>
    </AdapterCtx.Provider>
  );
}

export default function App() {
  return (
    <ThemeProvider>
      <Shell />
    </ThemeProvider>
  );
}
