# Frontend app shell (web/src/shell, components/Sidebar.tsx, components/Composer.tsx)

Built from observed public behaviour of ChatGPT and Claude; no source, CSS, assets or icons copied (icons are lucide-react, ISC).

## Layout
- `App.tsx` = providers only. `shell/AppShell.tsx` owns layout, routing, dialogs, shortcuts, scroll behaviour.
- Desktop: 260px sidebar, collapsible to a 52px icon rail (Claude-style); state is the `sidebarCollapsed` UI pref.
- Phone (<768px): sidebar is a modal drawer with focus trap, scrim and Esc to close.
- Artifact panel (components/ArtifactPanel.tsx) sits to the right; it auto-collapses the sidebar below 1400px.

## Sidebar
- Groups: Pinned, Today, Yesterday, Previous 7 days, Previous 30 days, then month names, then years (local calendar days).
- Row "…" menu: Export as Markdown, Rename (also double-click / F2), Pin/Unpin, Delete (also Delete key).
- Delete is soft for 5 s: the row disappears and a toast offers Undo; the store delete runs when the toast expires.
- Roving focus: the list is one tab stop; Arrow keys/Home/End move between chats.

## Search (Ctrl/⌘+K)
- Modal combobox over `TranscriptStore.search()` (optional method): titles AND message text, snippet with the match highlighted, opens the chat and scrolls to the matching message.
- The query lives in component state only: never in URL, storage or logs. A server-encrypted store must implement search server-side.

## Composer
- Auto-grows to 240px, send button morphs to Stop while streaming, IME-safe Enter.
- Enter sends / Shift+Enter newline by default; Settings can switch to Ctrl/⌘+Enter to send.
- **Drafts are per chat and memory-only** (`shell/drafts.ts`, a module Map). Not localStorage, not sessionStorage: an unsent draft can contain PHI (a pasted member id), and spec §6.2 allows only allowlisted UI prefs in browser storage. Drafts survive switching chats but not a reload. This is a deliberate gap versus ChatGPT/Claude.
- `/` opens a command menu: prompt templates (/denials, /trend, /carc, /ar) and actions (/new, /search, /export, /light, /dark, /system, /settings, /shortcuts).

## Routing
- `#/c/<opaque id>` per chat; Back/Forward switch chats. Ids are random and carry no PHI. With memory-only history an unknown id (after reload) falls back to a new chat.

## Themes
- Mode: System (default, follows `prefers-color-scheme` live), Light, Dark. Style: Neutral (ChatGPT-like) or Paper (Claude-like).
- Transitions are suppressed for the frame the theme flips; all motion is opacity/transform, short and ease-out, and `prefers-reduced-motion` disables it.

## Export / share
- Header Share: Copy as Markdown, Download .md. Settings > Data controls: export all chats. Uses the same serialiser as per-answer Copy, so PHI-flagged columns are dropped.

## Shortcuts
See `shell/shortcuts.ts` (also shown in Settings > Shortcuts and Ctrl/⌘+/).
