# CC Web Chat: UI Specification (v0.1)

**Status:** draft for build. **Date:** 2026-10-25. **Target repo:** github.com/Peter-Shamoun/cc, app lives in `web/`.
**Goal:** a React chat client for the CC analyst agent (healthcare EDI 837P/835 claims, DuckDB `sem.*` views) that looks and behaves as close as practical to the current ChatGPT and Claude web apps. Responses can be markdown, tables, CSV downloads, images and plots, Vega-Lite charts, SQL and code, and side-panel artifacts.

> **Clean-room rule.** We copy no proprietary source, CSS bundles, fonts, icons or logos from chatgpt.com or claude.ai. Behaviour and visual design are reproduced from public observation (help-centre docs, screenshots, community write-ups), and code comes only from permissively licensed open source, with notices kept. Proprietary fonts (OpenAI Sans, Anthropic Sans/Styrene, Tiempos) are **not** used. We use free substitutes.

Legend: **[S]** = sourced (citation in §9), **[O]** = observed in our own reference screenshot, **[E]** = estimate, needs verification against the live UI before pixel-polish.

---

## 1. Reference observations

### 1.1 ChatGPT (chatgpt.com)
- **Shell [O][S1]:** fixed left sidebar of about 260 px (`ChatGPT ▾` model menu, collapse toggle, then New chat, Images, search, projects, history list, and an account or upsell card at the bottom). Main column on a near-white background. In the empty state, a centred 24 px/600 heading ("Where should we begin?") sits above a pill-shaped composer about 410–770 px wide, with a "+" attach button on the left and mic and voice buttons on the right. Suggestion chips sit below, and a fine-print disclaimer (12 px) is pinned to the bottom. *Screenshot: `ref/chatgpt_home_empty_composer.png` (logged-out view, 1024 px wide).*
- **Thread width [S2][S3]:** controlled by the CSS var `--thread-content-max-width`, set to **40rem (640 px)** at container ≥ 768 px in the 2025–26 UI. It was 48rem (768 px) at ≥ 64rem in the older UI. Side gutter is `--thread-content-margin`: spacing 4, 6 at ≥ 37rem, and 16 at ≥ 72rem.
- **Messages:** user turns are right-aligned grey bubbles (about 70% max width, radius about 18–24 px **[E]**). Assistant turns have no bubble and no avatar, full thread width, plain markdown. Under the last assistant turn there's an action row: Copy, Thumbs up, Thumbs down, Read aloud, Edit-in-canvas or Share, Regenerate (a "Try again" menu with model switch), and a "…" menu. Actions on older turns show on hover. User turns get Copy and Edit on hover. Edit creates a branch, with a `‹ 2/2 ›` pager. **[E from observation]**
- **Streaming:** text renders incrementally. A pulsing filled dot (●) trails the last token while streaming. Before the first token, a "Thinking…" shimmer label shows (reasoning models show "Thought for N s" as a collapsible). Composer send turns into a **stop** square during streaming. Auto-scroll sticks to the bottom unless the user scrolls up, then a floating "↓" button appears. **[E]**
- **Tables [S4]:** markdown tables render inline. Data-analysis tables are **interactive**: scrollable, an **expand** control (two-arrow icon) top-right opens a full-screen view, a **download** button top-right exports **CSV**, and cells, rows or columns can be selected (Cmd/Ctrl-click, drag) to ask follow-ups about the selection.
- **Charts [S4][S5]:** static PNG charts, or interactive for bar, pie, scatter and line. The top-right has **download (PNG)**, **expand**, colour edit, and "Switch to interactive/static chart".
- **Code blocks [E]:** dark rounded container (radius about 8–16 px) with a header strip (language label on the left, "Copy code" on the right, "Edit" in newer builds). Monospace about 14 px. "Copied!" confirmation for about 2 s.
- **Canvas [S6]:** side-by-side editor. Toolbar arrows **browse and restore versions**, and **"Show changes"** diffs additions and deletions. There's a code-review shortcut for inline suggestions. The 2026 release notes say canvas has been removed for GPT-5.5 models in favour of in-chat "blocks". **Takeaway:** in-chat rich blocks first, side panel second.
- **Composer:** auto-growing textarea (max about 40% viewport, then scroll), "+" for attachments (files show as chips with icon, name, type and ✕ above the text), Enter to send, Shift+Enter for a newline, drag-and-drop anywhere. **[O][E]**

### 1.2 Claude (claude.ai)
- **Palette and type [S7][S8][S9]:** warm "paper" UI. Page `#F5F4ED` (parchment), elevated `#FAF9F5` (ivory), interactive sand `#E8E6DC`, dark page `#141413`, dark surface `#30302E`, accent terracotta `#D97757`, error `#B53333`, focus `#3898EC`. Text: secondary `#5E5D59`, tertiary `#87867F`. **Assistant prose is set in a serif** (Tiempos Text, about 16–17 px/1.6). UI chrome and user messages use a geometric sans (Styrene, "Anthropic Sans"). Headings inside answers use a serif display face.
- **Messages [E]:** user messages sit in a sand-tinted rounded card with an initial avatar. Assistant output has no card and a small asterisk mark at the end of the turn. The action row is Copy, Retry (with model menu), Thumbs up and down, shown under the latest turn and on hover elsewhere.
- **Artifacts [S10][S11]:** an artifact opens **in a panel beside the conversation**. In the chat it shows as a clickable card (icon, title, type, "Click to open"). The panel header has the title, a **Preview / Code** toggle, **Copy**, **Download**, Publish or Share, and Close (✕). The splitter between chat and panel is draggable **[E]**, and the chat column narrows. Versions: each revision creates a new version with a "v2 of 3"-style picker and ‹ › arrows **[E]**. Claude Code artifacts list versions under Share [S11]. Since 16 Sep 2026, new artifacts are template-based (docs, decks, designs) with **Export** to Word, PDF, Markdown and PPTX, and pre-September ones are "legacy" [S10]. All artifacts are listed in a sidebar "Artifacts" tab.
- **Thinking and tool use [S12][E]:** extended thinking shows as a collapsed row ("Thought process" plus a duration) that expands to the summarised reasoning in muted smaller type. Tool calls (web search, analysis tool, connectors) show as compact rows with an icon, label and result count, expandable to inputs and outputs. They stream "running…" with a shimmer.
- **Attachments [E]:** files and images show as thumbnail cards above the composer and inside the user message. Pasting a long text becomes a "Pasted" card.

### 1.2b Observations from reference screenshots in `ref/` (captured 2026-10-25, logged-out, 1024 px wide; the sidebar measures 138 px vs the known 260 px, so the scale is ≈ 0.53)
- **Thread width [O]:** assistant content and the composer span about 410 px in the screenshot, which is ≈ 770 px real, so **48rem** on a wide desktop. This confirms [S2]. Use `40rem` below about 1280 px container and `48rem` above.
- **User bubble [O]** (`chatgpt_response_top_heading_bullets_table.png`): right-aligned to the thread edge, pale tinted fill (logged-out build shows a light blue tint with blue text, close to `#E8F0FE` / `#2F6FD6` **[E]**; logged-in builds are neutral grey `#F4F4F4`), radius ≈ 18 px, padding ≈ 10×16 px, max width ≈ 70%.
- **Markdown in answers [O]:** heading h2 ≈ 20 px/600 then 16 px body. Bullets with **bold lead-ins**. An h3 ("Comparison table") ≈ 18 px/600. Italic caption under the table. Paragraph gap ≈ 12 px.
- **Markdown tables [O]** (`chatgpt_table_hover.png`): **no outer border, no zebra, no header fill**. Bold header row, 1 px hairline (`--border`) row dividers, cell padding ≈ 8×12 px, table stretches to full thread width, slightly smaller text (≈ 14 px). **No hover toolbar appeared on a plain markdown table**: the expand and download controls belong to *data-analysis* tables only [S4]. **Decision:** CC `table` parts get the toolbar (shown on hover and focus, top-right), and markdown-only tables keep the plain ChatGPT look.
- **Code block [O]** (`chatgpt_code_block_header_copy.png`): a single light-grey container (≈ `#F3F3F3` light **[E]**), radius ≈ 16–18 px, **no separate header bar**. A tiny language label (≈ 11–12 px, `--text-tertiary`) sits top-left inside the block, with an icon-only **copy** button top-right (≈ 28 px hit area). Body mono ≈ 13–14 px / 1.5. Light syntax theme (keywords red-brown, strings navy), comparable to Shiki `github-light`. Wraps long string literals visually. **Update to §4.6:** use this borderless, headerless style, with label and actions overlaid in the block's top padding (16 px). Our extra actions (Download, Open in panel, Re-run) live in a hover "…" next to copy.
- **CSV [O]** (`chatgpt_csv_block_and_login_for_files_prompt.png`): logged out, ChatGPT falls back to a `csv` code block plus text instructions, and an inline upsell card says "Log in to create and download files" (rounded 12 px, sand-grey fill, black pill primary button and outlined secondary). We always emit a real `file` chip and, for ≤ 50 rows, also a `table`. We never make the user copy CSV text by hand.
- **Message actions [O]:** under the last assistant turn, a left-aligned row of small (≈ 16 px icon, 28 px hit) icon buttons: copy, share. Logged in adds thumbs, regenerate and more. The row sits ≈ 8 px under the last paragraph.
- **Disclaimer and composer [O]:** "ChatGPT is AI and can make mistakes." 12 px tertiary, centred above the pinned composer. The composer is a white pill with a hairline border and soft shadow, height ≈ 52–56 px, "+" at left, mic and a **circular send** (accent fill, disabled = 40% opacity) at right. The empty state centres the composer vertically under a 24 px heading.
- **Claude artifact viewer [O]** (`claude_public_artifact_document_view.png`, `claude_public_artifact_interactive_app.png`, the public published-artifact page): a slim 40–44 px top bar with the serif "Claude" wordmark, small tertiary note "Content is user-generated and unverified.", and a **segmented two-icon toggle 👁 Preview / `</>` Code** directly beside it. Right side: view count, link icon, flag (report), a **"Copy"** text-plus-icon button and "Learn about artifacts". The artifact renders inside a **large rounded card (radius ≈ 12–16 px, 1 px border) on a light grey `#F5F5F5`-ish page**, with an inner scroll. Document artifacts: centred column ≈ 680 px real width, bold sans headings, 1 px horizontal rule between sections, dense 14–15 px body. **Adopt for §4.10:** the panel header uses the same segmented Preview/Code icon toggle at left next to the title, with Copy, Download and Close on the right. Content sits in an inset rounded card.
- `claude_login_wall.png`: the in-chat Claude UI wasn't reachable logged-out, so the chat-panel split view values stay **[E]**.
- `chatgpt_bar_chart_ascii_fallback.png`: logged out, ChatGPT drew a chart as ASCII or text instead of a rendered image (chart tooling needs login). This confirms that rendered charts are a logged-in data-analysis feature [S4][S5]. The same frame shows the **streaming Stop state** [O]: the send circle becomes a filled accent circle with a white square while generating.

### 1.3 Synthesis: the CC target look
Take **ChatGPT's layout and density** (system sans, 16/1.5, neutral greys, 40–48 rem thread, interactive data tables and charts) and add **Claude's artifact side panel** (split view, Preview/Code, versions, copy and download). Offer a theme switch: `neutral` (ChatGPT-like, default) and `paper` (Claude-like warm palette, serif prose). Both are token sets over the same components.

---

## 2. Layout and design tokens (a)

### 2.1 Layout
| Token | Value | Source |
|---|---|---|
| Sidebar width (expanded) | 260 px (range 260–280) | [S1][O] |
| Sidebar collapsed | 0 px with a floating 52 px header icon rail **[E]** | [O] |
| Header height | 52 px (56 px on mobile) | [S1] |
| `--thread-max` | **40rem** at ≥ 768 px container, `48rem` "wide" user setting | [S2][S3] |
| `--thread-gutter` | 16 px → 24 px at ≥ 592 px → 64 px at ≥ 1152 px | [S2] |
| Composer max width (observed ≈ 48rem wide desktop [O]) | = `--thread-max`, min-height 52 px, max-height 40vh **[E]** | [O] |
| Turn vertical gap | 24 px (section gap) | [S1] |
| User bubble max width | 70% of thread, padding 10 px 16 px **[E]** | — |
| Artifact panel default | 50% of main area, min 360 px, max 75%. Chat column min 400 px | [E], vercel/chatbot uses a fixed 400 px chat rail |
| Breakpoints | sm 640, md 768 (sidebar becomes a drawer below this), lg 1024 (artifact panel becomes a full-screen sheet below this), xl 1280 | Tailwind defaults |
| Table inline max height | 400 px with sticky header, then scroll. Expanded view is the panel or full screen | [E] |
| Image inline max | width 100% of thread, max-height 480 px, object-fit contain | [E] |

### 2.2 Typography
| Token | neutral (ChatGPT-like) | paper (Claude-like) |
|---|---|---|
| `--font-sans` | `ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", Roboto, "Helvetica Neue", Arial, sans-serif` [S1][S3] | `"Inter", ui-sans-serif, system-ui, -apple-system, sans-serif` (Styrene substitute) [S7] |
| `--font-prose` | = sans | `"Source Serif 4", Georgia, "Times New Roman", serif` (Tiempos substitute) [S8] |
| `--font-mono` | `ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace` [S3] | `"JetBrains Mono", ui-monospace, Menlo, monospace` **[E]** |
| body | 16 px / 1.5 (24 px) [S1] | 16.5 px / 1.6 [S9] **[E]** for the in-app value |
| caption, meta | 14 px / 1.43 [S1] | 14 px / 1.43 [S9] |
| fine print | 12 px / 1.33 **[E]** | 12 px / 1.6, weight 500 [S9] |
| code inline | 0.875em, padding 0.15em 0.35em, radius 4 px **[E]** | same |
| code block | 14 px / 1.6 **[E]** | 14–15 px / 1.6 [S9] |
| h1 / h2 / h3 in answers | 24/600, 20/600, 18/600, line-height 1.33 [S1]/**[E]** | 24/600, 20/600, 17/600 serif, line-height 1.3 [S8] |
| Empty-state heading | 24–28 px / 600 [S1][O] | 32 px serif / 500 **[E]** |

### 2.3 Colour tokens (CSS custom properties; dark values are estimates unless cited)
| Token | neutral light | neutral dark | paper light | paper dark |
|---|---|---|---|---|
| `--bg` (thread) | `#FFFFFF` [S1] | `#212121` **[E]** | `#F5F4ED` [S7] | `#262624` **[E]** |
| `--bg-sidebar` | `#F9F9F9` [S1] | `#181818` **[E]** | `#EFEDE5` **[E]** | `#1F1E1D` **[E]** |
| `--bg-elevated` (cards, composer, panel) | `#FFFFFF` | `#303030` **[E]** | `#FAF9F5` [S7] | `#30302E` [S7] |
| `--bg-user-bubble` | `#F4F4F4` **[E]** (≈ `#0000000d` over white [S1]) | `#303030` **[E]** | `#E8E6DC` [S7] | `#141413` [S7] |
| `--bg-hover` | `#0000000D` [S1] | `#FFFFFF0D` **[E]** | `#0000000A` **[E]** | `#FFFFFF0D` **[E]** |
| `--bg-code` | `#F9F9F9` header / `#FFFFFF` body, or full dark `#171717` **[E]** | `#171717` **[E]** | `#FAF9F5` / `#1F1E1D` **[E]** | `#1F1E1D` **[E]** |
| `--text` | `#0D0D0D` [S1] | `#ECECEC` **[E]** | `#141413` [S7] | `#FAF9F5` [S7] |
| `--text-secondary` | `#5D5D5D` [S1] | `#B4B4B4` **[E]** | `#5E5D59` [S9] | `#B0AEA5` [S9] |
| `--text-tertiary` | `#8F8F8F` [S1] | `#8F8F8F` **[E]** | `#87867F` [S9] | `#87867F` |
| `--border` | `#0000001A` [S1] | `#FFFFFF1A` **[E]** | `#0000001A` **[E]** (warm) | `#FFFFFF14` **[E]** |
| `--border-strong` | `#E6E6E6` [S1] | `#424242` **[E]** | `#E8E6DC` | `#3D3D3A` [S9] |
| `--accent` (send, focus, links) | `#0D0D0D` button / `#2964AA` links **[E]** | `#FFFFFF` button | `#D97757` [S8] | `#D97757` [S8] |
| `--focus-ring` | `#0285FF` **[E]** | same | `#3898EC` [S9] | same |
| `--danger` | `#E02E2A` **[E]** | `#FF6B6B` **[E]** | `#B53333` [S9] | `#E26D6D` **[E]** |
| `--warning-bg` (assumptions callout) | `#FFF8E6` **[E]** | `#3A3020` **[E]** | `#F7EED9` **[E]** | `#3A3326` **[E]** |
| `--scrim` | `#00000080` [S1] | `#000000B3` | same | same |
| Chart palette (8) | Tableau10-style, colour-blind safe: `#4C78A8 #F58518 #54A24B #E45756 #72B7B2 #EECA3B #B279A2 #9D755D` (Vega default) | same | warm-shifted: `#D97757 #6A9BCC #788C5D #C46686 #B8A16B #5E5D59 #3898EC #B53333` **[E]** | same |

### 2.4 Radii, spacing, elevation
- Spacing scale (4 px base): 2, 4, 6, 8, 10, 12, 16, 20, 24, 32, 48, 64 [S1 lists 6–20].
- Radii: buttons and nav items **10 px** [S1], cards and tables **12 px** **[E]**, code blocks **12 px** **[E]**, composer **28 px** (pill, single-line) easing to 24 px when multiline [O]/**[E]**, user bubble **18 px** **[E]**, chips **9999 px**, artifact panel **16 px** on its inner edge **[E]**.
- Elevation: composer `0 0 0 1px var(--border), 0 4px 16px rgba(0,0,0,.05)` [O]/**[E]**. The paper theme uses warm shadow `rgba(40,30,20,.06)` [S8]. Menus `0 8px 24px rgba(0,0,0,.12)`.
- Motion: 150 ms for hover and press, 200–250 ms ease-out for panel slide and resize (respect `prefers-reduced-motion`). Streaming dot pulse 1 s infinite.
- Icons: **Lucide** (ISC), 16–20 px, stroke 1.75–2. Never use OpenAI or Anthropic glyphs.

---

## 3. App shell and chrome components

1. **`<Sidebar>`:** New chat (⌘⇧O), Search chats (⌘K), list of conversations grouped Today, Yesterday, Previous 7 days and older (titles are generated server-side and **PHI-scrubbed**, see §6), and Artifacts library (Claude-style). Hover a row to see "…" (Rename, Delete). The footer has the user and role and a link to the environment (dev, staging, prod) badge. Collapse button. On mobile it's a drawer.
2. **`<Header>`:** analyst profile or model picker ("CC Analyst ▾": strategy variants such as direct-SQL or metric-spec for internal users), dataset scope chip (e.g. `payer: all · 2024-01..2025-09 · tenant: X`), a **PHI mode badge** (`De-identified` green or `Limited data set` amber, from the server), Share (disabled when PHI is present) and theme toggle.
3. **`<Thread>`:** virtualised list (TanStack Virtual) of turns. Stick-to-bottom while streaming with a "↓ Jump to latest" floating button. Keyboard: ↑ in an empty composer edits the last user message.
4. **`<UserMessage>`:** right-aligned bubble showing attachments as chips, then the text (plain, whitespace-preserved, no markdown). Hover actions: Copy, Edit (inline editor that creates a branch on send). Branch pager `‹ 1/2 ›`.
5. **`<AssistantMessage>`:** ordered list of **parts** (§4), each rendered by a part component. A footer action row (§4.12). A status line while running ("Planning… → Querying sem.claims_paid… → Checking results…") driven by `status` parts.
6. **`<Composer>`:** auto-grow textarea, "+" menu (Upload file, which is CSV or XLSX for **reference lists only** with the PHI warning in §6, plus Insert saved question). Send is a 36 px circle (accent), Stop is a square while streaming. Enter sends, Shift+Enter is a newline, IME-safe (`isComposing`). Drag-and-drop overlay. Character counter after 4k. Slash commands: `/sql` (show SQL first), `/chart`, `/explain`. Disclaimer under the composer: *"CC can make mistakes. Check figures against source before use."*
7. **`<EmptyState>`:** greeting heading and 4 suggestion chips (e.g. "Denial rate by payer, last 6 months"), same as ChatGPT's centred composer [O].
8. **`<ArtifactPanel>`:** see §4.10.

---

## 4. Response-part components (b)

All parts share a **`<PartFrame>`** wrapper: full thread width, 12 px radius, `--border` 1 px, header strip (32–36 px, 13 px label, secondary text) with right-aligned icon buttons (tooltip on hover, `aria-label`), body, and optional footer (provenance: "from `sem.claims_paid` · 1,284 rows · 0.42 s"). Markdown is the exception and has no frame. Every part is focusable, and its buttons are reachable by keyboard.

### 4.1 Markdown (`text`)
- Rendered with **Streamdown** (handles unterminated markdown mid-stream). Fallback is react-markdown with remark-gfm, remark-math, rehype-katex and rehype-sanitize.
- GFM: headings, lists (task lists read-only), blockquote, tables (small inline tables use **Table-lite**: same frame without the expand button when ≤ 20 rows; larger ones convert to a `table` part server-side), strikethrough, footnotes, autolinks. Math via KaTeX (`$…$`, `$$…$$`).
- Links open in a new tab with `rel="noopener noreferrer nofollow"`. **No remote images** in markdown (blocked by CSP and sanitizer). Images come only as `image` parts. No raw HTML.
- Prose rhythm: paragraph margin 0.75em (neutral) or 1em (paper), list item 0.25em, h2 top margin 1.5em. Max line length governed by `--thread-max`.
- Streaming caret: an 8 px `--text` dot inline after the last character, `animate-pulse`, removed on `text.end`.
- Citations to rows or tables use `[^n]` footnotes, which render as superscript chips that scroll to the referenced part.

### 4.2 Table (`table`)
- **Inline:** sticky header, zebra off, row hover `--bg-hover`, numeric columns right-aligned and tabular-nums, formatted by column `format` (currency `$1,234.56`, percent, integer, date ISO). Nulls show as muted "—". Max 400 px tall. Shows first `previewRows` (default 50) of `rowCount`, with a footer "Showing 50 of 1,284 rows · Open full table".
- **Header actions (ChatGPT parity [S4]):** **Expand** (⤢) opens the table in the ArtifactPanel (≥ lg) or full-screen sheet. **Copy** puts TSV of the visible page on the clipboard (pastes into Excel or Sheets). **Download CSV** (⤓) uses `csvRef` (server-generated, the full result) when present, otherwise client CSV of loaded rows via Papa Parse, filename `cc_<slug>_<yyyy-mm-dd>.csv` (no PHI in filename). An "…" menu offers Download XLSX (phase 2) and "Show SQL" (scrolls to the linked `code` part).
- **Interactions:** click a header to sort (asc → desc → none, client-side on loaded rows; for server-paged tables, sends a `sort` request), column resize by dragging the header edge, column visibility menu, and in the expanded view a global filter box. **Cell selection** (click, Shift-click range, Cmd/Ctrl multi-select [S4]) shows a floating "Ask about selection" pill that inserts a structured reference (`@table T3 rows 4–9, col paid_amt`). The values aren't pasted into the prompt.
- Virtualised rows in the expanded view (TanStack Virtual), so 100k rows stay smooth. Server paging via `GET /api/results/:resultId?page=…` (POST body for filters; never querystring PHI).
- **Masking:** columns flagged `phi: true` in the schema render masked (`•••• 4821`) unless the session's PHI mode allows them, and they're excluded from Copy and CSV unless allowed (§6).
- Empty result: the frame shows "Query returned 0 rows" plus the "Show SQL" link.

### 4.3 CSV / file chip (`file`)
- Chip card about 280×56 px: file-type icon (CSV green, XLSX, PDF, PNG), name, meta "CSV · 1,284 rows · 212 KB", Download button. If `previewable`, clicking the card opens a table preview in the ArtifactPanel.
- Download through `GET /api/files/:fileId` (opaque id, short-lived signed **POST-to-GET exchange** or `Authorization` header via `fetch` → Blob → `URL.createObjectURL`, revoked after 60 s). Never place a signed URL with PHI-bearing parameters in the address bar. Server sets `Content-Disposition`.
- Expiry: chip shows "Expired, re-run to regenerate" after `expiresAt`.

### 4.4 Image / plot (`image`)
- Inline image (server-rendered PNG or SVG from matplotlib or Altair). Rounded 12 px, `--border`, max-height 480 px, alt text required (from `alt` field; generated chart description).
- Hover toolbar top-right (ChatGPT parity [S5]): **Expand** (lightbox), **Download** (PNG, or SVG if available), Copy image (Clipboard API `image/png`).
- **Lightbox:** yet-another-react-lightbox with zoom (wheel, pinch), pan, Esc to close, ← → between images in the same turn, scrim `--scrim`.
- Loading: skeleton with fixed aspect ratio (`width`/`height` from the part) so nothing jumps. Error: "Image unavailable" tile.
- Images arrive as `blob` ids fetched with auth, never as third-party URLs (CSP `img-src 'self' blob: data:`).

### 4.5 Vega-Lite chart (`chart`)
- Rendered client-side with **vega-embed** (via `react-vega` or a thin wrapper). Data is inline `values` (≤ 5k rows) or `dataRef` → fetched JSON. The renderer is `svg` for crispness, `canvas` above 2k marks.
- Theme: our token palette via Vega `config` (font = `--font-sans`, label colour `--text-secondary`, gridlines `--border`). Dark mode re-renders with a dark config.
- Header actions: **Expand** (panel or full screen, responsive `width: "container"`), **Download PNG** (`view.toImageURL('png', 2)`), Download SVG, **Download data (CSV)**, "View spec" (opens a JSON `code` view), and a toggle **Interactive ↔ Static** (ChatGPT parity [S5]; static = pre-rendered PNG).
- Tooltips: Vega tooltip handler with formatted values. **PHI columns are never bound to tooltip or text marks unless allowed.** The server validates the spec. Interactive selections (brush) are optional for phase 2.
- **Spec safety:** reject specs with `url` data sources pointing off-origin, `expr` functions beyond the allowlist, or `usermeta` scripts. Use `vega-interpreter` (CSP-safe, no `eval`).

### 4.6 SQL and code block (`code`)
- Header: language label (`sql`, `python`, `json`), optional title ("Query 2 · sem.claims_paid"), buttons **Copy** (→ "Copied ✓" 2 s), **Download** (`.sql`), **Open in panel** (for > 30 lines), and for SQL **"Re-run"** (internal users only; sends a `rerun` request, never executes client-side).
- Body: Shiki highlighting (theme `github-light` / `github-dark`, or `vitesse` for the paper theme), 14 px mono, line-height 1.6, horizontal scroll with no wrap, and a wrap toggle in the "…" menu. Line numbers in the panel view only. Code > 25 lines collapses to 25 with a "Show more" fade.
- SQL parts appear **collapsed by default** under an "SQL ▸ 1 query" disclosure in the assistant turn (analyst transparency without clutter). An expand-all user preference makes them always open.
- Streaming: tokens appended. Highlight runs after a 100 ms debounce, so the dot caret also appears in code.

### 4.7 Assumptions and caveats callout (`callout`)
- Left 3 px accent bar, `--warning-bg` background, 12 px radius, icon (ⓘ info / ⚠ warning / ⛔ blocking), title (e.g. "Assumptions"), bulleted markdown body.
- Variants: `assumption` (info), `caveat` (warning, e.g. "835s for March not yet loaded"), `data_quality` (warning), `phi_redaction` (shield icon: "2 columns were masked under your access level").
- Collapsible when > 4 bullets. Assumptions are **always visible above the fold of the answer** (rendered right after the first text paragraph if the backend emits them early).
- Interaction: each assumption may have `editable: true` and shows "Change" → prefills the composer ("Instead, assume …").

### 4.8 Tool-call, step and thinking disclosure (`tool_call`, `reasoning`, `status`)
- **Reasoning:** a single collapsed row "Thought for 6 s ▸" (while running: shimmering "Thinking…"). Expanded, it shows the summarised reasoning in 14 px `--text-secondary`, left border, no markdown headings. Claude/ChatGPT parity [S12].
- **Tool calls:** compact rows, one per call, grouped as "Used 3 tools ▸" when > 2. Each row has an icon by tool (database = `run_sql`, list = `describe_view`, chart = `make_chart`, check = `verify`), a label ("Queried sem.claims_paid"), a state pill (`running` spinner, `ok` ✓ with duration and row count, `error` ✕), and expand to see **input** (SQL or JSON args, as a `code` view) and **output summary** (row count, columns, first 5 rows **masked**). Raw outputs are fetched lazily by id.
- **Multi-agent** (for the "multiagent" strategy): steps show as a vertical timeline (planner → analyst → verifier) with each agent's tool rows nested.
- Disclosures persist their open or closed state per message in memory only (not localStorage).

### 4.9 Error state (`error`)
- Inline frame with `--danger` left bar. Title ("Query failed", "Access denied", "Timed out", "Connection lost"), human message, optional `details` (collapsed, mono; **server must strip PHI** from DB error text), and actions: **Retry** (re-sends the turn), "Edit question", and for `retryable:false` "Contact admin".
- Stream drop: the partial answer stays, with a footer "Response interrupted · Retry". Network offline banner at the top of the thread.
- Auth expiry (STS 900 s session [cc]): the client refreshes silently. If refresh fails, a modal asks to re-auth, and the draft composer text is preserved in memory only.
- Rate limit: inline toast with countdown.

### 4.10 Artifact panel (`artifact`)
- **In-thread card** (Claude parity [S10]): 64 px card with type icon, title, subtitle ("Table · v2", "Chart", "Report"), and "Open" on click. The latest artifact auto-opens on first creation at ≥ lg if the user hasn't closed the panel in this session.
- **Panel:** right side, resizable via a 6 px drag handle (react-resizable-panels; default 50%, min 360 px, double-click resets), slide-in 250 ms. Header: title (editable inline), **version picker** `‹ v2 of 3 ›` plus a "Show changes" diff toggle (ChatGPT canvas parity [S6]; text and code use a line diff, tables a row-count or column diff), tabs **Preview | Code** (or **Table | SQL**, or **Chart | Spec | Data**), **Copy**, **Download** (format menu: CSV, PNG, SVG, MD, `.sql`), **Full screen**, **Close ✕** (Esc).
- Artifact kinds: `table`, `chart`, `report` (markdown doc assembled from parts, exportable to MD and, in phase 2, PDF and DOCX), `code`, `image`. Kind registry modelled on vercel/chatbot's `artifacts/{text,code,sheet,image}` handler pattern [S13].
- Streaming into the panel: the content updates live, with the version locked until `artifact.end`.
- Below lg, the panel is a full-screen sheet with a back arrow.

### 4.11 Attachments in user turns
- Image thumbnails (64 px square) and file chips. Uploads are **reference files** (code lists, payer lists). Upload warning modal on first use: "Don't upload files containing PHI unless your workspace is approved for it." The server scans and may reject.

### 4.12 Message action row
- Under each assistant turn: **Copy** (markdown of text parts plus tables as GFM, no masked PHI), **Thumbs up**, **Thumbs down** (opens a feedback popover: reason chips "Wrong number", "Wrong SQL", "Unclear", "Other" plus free text; **sends question id and part ids only, never row data**), **Regenerate** (menu: "Try again", "Try with strategy ▸"), **"…"** (Copy SQL, Download all as ZIP, Report a PHI concern). Visible on the latest turn, otherwise on hover or focus.
- Token, cost and latency meta (internal role only): small muted text "4 queries · 3.1 s".

---

## 5. Response stream contract (c)

Transport: `POST /api/chat` returns **Server-Sent Events** (`text/event-stream`), one JSON event per `data:` line. It's compatible in spirit with the AI SDK UI message stream (text-start/delta/end plus `data-*` parts), so `@ai-sdk/react` `useChat` can consume it via a custom transport, or our own reducer can. The request body carries `conversationId`, `messages` (ids and text only), and `options`. **No PHI in the URL.**

```ts
// web/src/contract/stream.ts — CC response stream v1
export type ID = string;               // opaque, non-PHI (ulid)
export type ISODate = string;

/** Sensitivity of anything rendered. Server is authoritative. */
export type Sensitivity = "none" | "deidentified" | "limited" | "phi";

export interface PartBase {
  id: ID;                // unique within message
  parentId?: ID;         // nest under a tool_call / agent step
  sensitivity?: Sensitivity;
  createdAt?: ISODate;
}

/* ---------- Columnar data ---------- */
export type ColumnType = "string" | "integer" | "number" | "currency" | "percent"
  | "date" | "datetime" | "boolean" | "code";        // code = ICD/CPT/CARC etc.
export interface Column {
  key: string;
  label: string;
  type: ColumnType;
  format?: string;        // e.g. "$,.2f", ".1%"
  phi?: boolean;          // masked unless session allows
  description?: string;   // tooltip from sem.* catalog
}

/* ---------- Part payloads ---------- */
export interface TextPart      extends PartBase { type: "text"; text: string; }          // full text once complete
export interface ReasoningPart extends PartBase { type: "reasoning"; summary: string; durationMs?: number; }
export interface StatusPart    extends PartBase { type: "status"; label: string; }          // transient progress line

export interface ToolCallPart extends PartBase {
  type: "tool_call";
  tool: "run_sql" | "describe_view" | "list_views" | "make_chart" | "verify" | "search_examples" | string;
  label: string;                         // "Queried sem.claims_paid"
  state: "running" | "ok" | "error";
  input?: { sql?: string; args?: Record<string, unknown> };
  output?: { rowCount?: number; columns?: Column[]; preview?: unknown[][]; message?: string };
  durationMs?: number;
  agent?: string;                        // "planner" | "analyst" | "verifier"
}

export interface TablePart extends PartBase {
  type: "table";
  title?: string;
  columns: Column[];
  rows: unknown[][];                     // preview rows (row-major, aligned to columns)
  rowCount: number;                      // full result size
  resultId?: ID;                         // server-paged access to the full result
  csvFileId?: ID;                        // pre-built full CSV
  sqlPartId?: ID;                        // link to the code part that produced it
  source?: { views: string[]; elapsedMs?: number; asOf?: ISODate };
}

export interface FilePart extends PartBase {
  type: "file";
  fileId: ID;
  name: string;                          // no PHI in names
  mime: "text/csv" | "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
      | "application/pdf" | "image/png" | "image/svg+xml" | "application/zip" | string;
  sizeBytes: number;
  rowCount?: number;
  previewable?: boolean;
  expiresAt?: ISODate;
}

export interface ImagePart extends PartBase {
  type: "image";
  blobId: ID;                            // fetched with auth -> object URL
  mime: "image/png" | "image/svg+xml" | "image/webp";
  width: number; height: number;
  alt: string;                           // required, generated description
  caption?: string;
  svgBlobId?: ID;
}

export interface ChartPart extends PartBase {
  type: "chart";
  library: "vega-lite";
  spec: Record<string, unknown>;         // Vega-Lite v5/v6 spec WITHOUT data.url
  data?: { name: string; values: Record<string, unknown>[] }; // ≤ 5k rows inline
  dataResultId?: ID;                     // else fetched
  staticImageBlobId?: ID;                // for Interactive↔Static toggle & export
  title?: string;
  alt: string;
}

export interface CodePart extends PartBase {
  type: "code";
  language: "sql" | "python" | "json" | "text" | string;
  code: string;
  title?: string;
  role?: "query" | "snippet" | "spec";
  collapsed?: boolean;                   // default true for role=query
  rerunnable?: boolean;
}

export interface CalloutPart extends PartBase {
  type: "callout";
  variant: "assumption" | "caveat" | "data_quality" | "phi_redaction" | "info";
  title?: string;
  items: { id: ID; text: string; editable?: boolean }[];  // markdown inline allowed
}

export interface ArtifactPart extends PartBase {
  type: "artifact";
  artifactId: ID;
  version: number;
  kind: "table" | "chart" | "report" | "code" | "image";
  title: string;
  content: TablePart | ChartPart | CodePart | ImagePart | { markdown: string };
  autoOpen?: boolean;
}

export interface ErrorPart extends PartBase {
  type: "error";
  code: "query_failed" | "access_denied" | "timeout" | "rate_limited" | "auth_expired"
      | "validation" | "internal" | string;
  title: string;
  message: string;                       // PHI-free
  details?: string;                      // PHI-scrubbed technical detail
  retryable: boolean;
  retryAfterMs?: number;
}

export type Part = TextPart | ReasoningPart | StatusPart | ToolCallPart | TablePart | FilePart
  | ImagePart | ChartPart | CodePart | CalloutPart | ArtifactPart | ErrorPart;

/* ---------- Stream events ---------- */
export type StreamEvent =
  | { type: "message.start"; messageId: ID; conversationId: ID; model?: string; strategy?: string }
  | { type: "text.start"; partId: ID }
  | { type: "text.delta"; partId: ID; delta: string }
  | { type: "text.end"; partId: ID }
  | { type: "reasoning.delta"; partId: ID; delta: string }
  | { type: "reasoning.end"; partId: ID; durationMs: number }
  | { type: "code.delta"; partId: ID; delta: string }        // stream SQL as it is written
  | { type: "part"; part: Part }                             // add or replace a whole part (idempotent by id)
  | { type: "part.patch"; partId: ID; patch: Partial<Part> } // e.g. tool_call state running -> ok
  | { type: "status"; label: string | null }                 // null clears
  | { type: "message.end"; messageId: ID; finishReason: "stop" | "error" | "aborted";
      usage?: { queries: number; elapsedMs: number } }
  | { type: "heartbeat" };                                   // every 15 s

/** Persisted message shape (what transcript storage saves). */
export interface AssistantMessage {
  id: ID; role: "assistant"; parts: Part[]; createdAt: ISODate;
  parentId?: ID;                         // branching
  feedback?: "up" | "down";
}
export interface UserMessage {
  id: ID; role: "user"; text: string; attachments?: FilePart[]; createdAt: ISODate; parentId?: ID;
}
```

**Rules**
1. Part order in the message = order of first appearance in the stream. `part` with an existing id replaces it (enables "SQL draft → final").
2. The client must ignore unknown part types gracefully (render a muted "Unsupported content" chip) for forward compatibility.
3. The server validates every part against a JSON Schema (generated from these types with `ts-json-schema-generator`) before emitting. The client re-validates with zod in dev builds.
4. Abort = client closes the stream (`AbortController`) → `POST /api/chat/:messageId/stop` (body-less) so the backend cancels DuckDB work.
5. Resume: SSE `id:` per event, so the client can reconnect with `Last-Event-ID` (in-memory buffer server-side, 60 s).

---

## 6. PHI and HIPAA constraints for the UI (e)

1. **URLs carry no PHI.** Routes are `/c/:conversationId` with opaque ulids. No query strings with filters, member ids, names, dates of service or claim numbers. Table paging, filters and sorts go in POST bodies. Error pages never echo input.
2. **No browser persistence of data.** Nothing from responses goes in `localStorage`, `sessionStorage`, IndexedDB, Cache Storage, or service-worker caches. The only `localStorage` keys allowed are UI prefs (`theme`, `sidebarCollapsed`, `threadWidth`), enforced by a wrapper `prefs.ts` with a key allowlist and an ESLint rule banning direct `localStorage` use. Composer drafts live in memory. HTTP responses carry `Cache-Control: no-store`. Disable bfcache for chat routes (`Cache-Control: no-store` covers it), and the service worker (if any) is network-only for `/api/*`.
3. **No telemetry of row data.** Analytics (if any, self-hosted, e.g. PostHog self-host or OpenTelemetry to our collector) send event names, part *types*, counts and timings only. Never text, cell values, SQL literals, filenames, or titles. Session replay tools are **banned**. Error reporting (Sentry self-hosted or OTel) uses `beforeSend` scrubbing: drop `breadcrumbs.console`, request bodies, and DOM text, and add a CI test asserting the scrubber.
4. **Pluggable transcript storage.** The client talks to a `TranscriptStore` interface through the API only. The backend implements one of the two designs under evaluation (bc-af8d3012 work):
   ```ts
   export interface TranscriptStore {
     listConversations(cursor?: string): Promise<{ items: {id: ID; title: string; updatedAt: ISODate}[]; next?: string }>;
     getConversation(id: ID): Promise<{ id: ID; messages: (UserMessage|AssistantMessage)[] }>;
     appendMessage(conversationId: ID, msg: UserMessage|AssistantMessage): Promise<void>;
     deleteConversation(id: ID): Promise<void>;        // hard delete + audit event
     setRetention(policy: { days: number }): Promise<void>;
   }
   ```
   Options: `ephemeral` (in-memory, gone on reload, default for PHI-mode sessions), `server-encrypted` (KMS-encrypted at rest, tenant-scoped, audit-logged), and `references-only` (store question and part metadata plus `resultId`s that are re-materialised under the user's current permissions, so no row data at rest). A server feature flag selects it, and the UI shows a "History off" badge when ephemeral.
5. **Titles and sidebar** are generated server-side from a PHI-scrubbed summary ("Denial rate by payer, 2025"). They never include member names, MRNs or claim ids. Users can rename. The rename is validated by the same scrubber.
6. **Masking by default.** Columns with `phi: true` render masked. Unmasking requires role ≥ `phi_viewer` and is per-session, with a reason prompt that writes an audit event. Copy, CSV and chart exports respect the mask state. Downloads are audit-logged server-side (user, fileId, rowCount, never content).
7. **Clipboard and download hygiene.** Copy-message excludes masked values. Downloads use Blob object URLs revoked after use. Filenames never include PHI. A download confirmation shows when `sensitivity ∈ {limited, phi}`.
8. **Session.** Idle timeout of 15 min (configurable) with a 60 s warning modal, then the thread view is cleared from memory and the page re-auths. Tokens live in an httpOnly, SameSite=Strict cookie (never JS-readable). The STS 900 s scoped session refresh is server-side.
9. **Content Security Policy:** `default-src 'self'; img-src 'self' blob: data:; connect-src 'self'; script-src 'self'` (no `unsafe-eval`; use vega-interpreter); `frame-ancestors 'none'`; no third-party fonts or CDNs (self-host Inter and Source Serif 4). `Referrer-Policy: no-referrer`.
10. **Sharing** is disabled for conversations whose parts have `sensitivity ≥ limited`. Publish-artifact links (Claude-style) only for `none` or `deidentified`, and only within the tenant.
11. **Rendering safety.** Markdown is sanitised (rehype-sanitize), with no raw HTML, no remote images, and Vega specs validated. Model output can never inject a script or a fetch to a third-party origin (exfil defence).
12. **Accessibility is not optional** for an enterprise healthcare tool: WCAG 2.2 AA contrast for both themes (check `#87867F` on `#F5F4ED` ≈ 3.4:1, so use it for ≥ 14 px meta only, not body), focus rings visible, `aria-live="polite"` on the streaming region (throttled), and tables with proper `<th scope>`.

---

## 7. Recommended stack and licences (d)

| Concern | Choice | Licence | Notes |
|---|---|---|---|
| Base template | **vercel/chatbot** (formerly ai-chatbot) | Apache-2.0 [S13] | Fork into `web/`, keep `LICENSE` and a `NOTICE` crediting Vercel. Reuse the artifact kind registry pattern (`artifacts/{text,code,sheet,image}`), the panel and the version diff. Strip Next-auth, Postgres and Blob. Wire to our API |
| Framework | Next.js (App Router) + React 19, or Vite + React if served by our Python backend | MIT | Next matches the template. Vite SPA is simpler for an internal tool; both work |
| Streaming hooks | Vercel AI SDK `ai` / `@ai-sdk/react` (`useChat` with a custom transport) | Apache-2.0 | Or a 150-line custom SSE reducer |
| Chat primitives (optional) | **assistant-ui** | MIT | Thread, Composer, ActionBar, BranchPicker primitives, auto-scroll and tool UI registry. Good if we leave the Vercel template |
| Streaming markdown | **Streamdown** 2.x | Apache-2.0 [S14] | Drop-in react-markdown replacement. Handles incomplete blocks, built-in Shiki, KaTeX and Mermaid |
| Markdown fallback | react-markdown + remark-gfm + remark-math + rehype-katex + rehype-sanitize | MIT (all) | |
| Math | KaTeX | MIT | Self-host fonts |
| Syntax highlighting | Shiki | MIT | Lazy-load grammars: sql, python, json, bash |
| Tables | TanStack Table v8 + TanStack Virtual | MIT | Sorting, resizing, visibility, selection |
| CSV | Papa Parse | MIT | Client export of loaded rows |
| Charts | Vega-Lite + vega-embed + vega-interpreter (react-vega optional) | BSD-3-Clause | The CSP-safe interpreter is needed. Recharts (MIT) only if hand-built charts are needed |
| Lightbox | yet-another-react-lightbox | MIT | Zoom plugin |
| Panels | react-resizable-panels | MIT | Artifact splitter |
| UI kit | shadcn/ui (copy-in) on Radix primitives + Tailwind CSS v4 | MIT | Tokens in §2 become CSS vars and the Tailwind theme |
| Icons | lucide-react | ISC | |
| State | Zustand (in-memory only, no `persist` middleware) | MIT | ESLint ban on `persist` |
| Diff | `diff` (jsdiff) | BSD-3-Clause | Artifact "Show changes" |
| Validation | zod | MIT | Dev-time stream validation |
| Fonts | Inter, Source Serif 4, JetBrains Mono, self-hosted | SIL OFL 1.1 | Substitutes for proprietary faces |
| Testing | Vitest, Testing Library, Playwright (visual snapshots vs `ref/` screenshots), axe-core | MIT / Apache-2.0 / MPL-2.0 | |

**Study only, don't import code (licence risk):**
- **LibreChat:** MIT, safe to borrow from with attribution. Good reference for artifacts (it has a Claude-style artifacts implementation with Sandpack), branching, and multi-provider message shapes.
- **Open WebUI:** custom "Open WebUI License" since v0.6.6 (BSD-3 based, plus a **branding-preservation clause** above 50 users unless enterprise-licensed) [S15]. **Study only.**
- **Lobe Chat:** "LobeHub Community License", where **derivative distribution needs a commercial licence** [S16]. **Study only.**
- ChatGPT and Claude: proprietary. Observe only. No CSS or JS copy, no font or asset hotlinking.

---

## 8. Prioritised build checklist (f)

**P0: walking skeleton (day 1–2)**
- [ ] Fork vercel/chatbot into `web/`, keep LICENSE and NOTICE, remove auth, DB and blob. Add Tailwind tokens from §2 (neutral light and dark).
- [ ] `contract/stream.ts` (§5) plus a JSON Schema export. A **mock SSE server** (`web/mock/`) replaying fixture conversations that exercise every part type (no backend needed, no PHI, synthetic data from `ccsim`).
- [ ] Stream reducer: text deltas, `part`, `part.patch`, status, abort, and unknown-part fallback.
- [ ] Shell: sidebar, header, thread with stick-to-bottom, composer (Enter or Shift+Enter, Stop), empty state matching `ref/chatgpt_home_empty_composer.png`.
- [ ] Markdown via Streamdown with the streaming dot. Code block with Shiki and Copy.
- [ ] PHI guardrails baked in from the start: `prefs.ts` allowlist plus ESLint rule, CSP headers, no-store, opaque routes.

**P1: data parts (day 3–5)**
- [ ] Table part: TanStack, sort, sticky header, Copy TSV, Download CSV (server `csvFileId` or Papa), "n of N rows", masked PHI columns.
- [ ] File chip with authenticated Blob download.
- [ ] Image part plus lightbox plus download.
- [ ] Vega-Lite chart with token theme, expand, PNG, SVG and CSV download, Interactive or Static toggle, vega-interpreter under CSP.
- [ ] SQL disclosure (collapsed), callout component (assumption, caveat, PHI-redaction), error part plus retry.
- [ ] Tool-call and reasoning disclosures with running, ok and error states.
- [ ] Message action row (copy, thumbs with PHI-free feedback payload, regenerate).

**P2: artifacts and parity polish (week 2)**
- [ ] Artifact panel: resizable, tabs (Preview/Code, Table/SQL, Chart/Spec/Data), versions plus "Show changes", copy, download, full screen, Esc, mobile sheet.
- [ ] Expanded table view: virtualisation, filter, column resize and visibility, cell-selection "Ask about selection".
- [ ] Edit user message plus branch pager. Regenerate with strategy menu.
- [ ] Paper (Claude-like) theme plus dark variants. Thread-width preference.
- [ ] Playwright visual comparisons against new `ref/` screenshots (ChatGPT and Claude, logged-in, same viewport). Tune the [E] tokens until the diff is within tolerance.

**P3: hardening (week 3)**
- [ ] Wire `TranscriptStore` to the chosen backend design. Ephemeral mode badge.
- [ ] Idle timeout, re-auth modal, PHI unmask flow with audit reason, download confirmation for limited or PHI.
- [ ] Telemetry scrubber tests (assert no cell values leave the browser). An axe-core a11y pass, contrast audit, keyboard-only walkthrough.
- [ ] Server-paged results for > 50k rows. Resume via `Last-Event-ID`.
- [ ] XLSX export, report artifact export to PDF and DOCX, Mermaid (optional).

**Definition of done for "looks like ChatGPT/Claude":** side-by-side screenshots at 1440×900 and 390×844, both themes. Thread width, type scale, composer shape, action-row placement and artifact panel behaviour match within the tolerances above. An independent reviewer can't spot structural differences, only branding.

---

## 9. Sources
- [S1] Design Atlas, "ChatGPT" tokens (colours, 16/1.5 body, 14/1.43 caption, 24/600 heading, 10 px radii, 260–280 px sidebar, 52 px header): https://geoffreymanda.github.io/design-atlas/styles/chatgpt.html (third-party analysis, so treat values as observed estimates)
- [S2] Dark Reader issue #14162 (ChatGPT `--thread-content-max-width: 48rem` at ≥ 64rem; `--thread-content-margin` spacing 4/6/16 at 37rem/72rem): https://github.com/darkreader/darkreader/issues/14162
- [S3] alexchexes gist "ChatGPT web-interface width fix" (current `--thread-content-max-width: 40rem` at ≥ 768 px) and the sickn33 userstyle (font stacks): https://gist.github.com/alexchexes/d2ff0b9137aa3ac9de8b0448138125ce, https://greasyfork.org/en/scripts/578534-chatgpt-wide-centered-sickn33/code
- [S4] OpenAI Help, "Extracting insights with ChatGPT data analysis" (interactive tables, expand, CSV download, cell selection, chart download and expand): https://help.openai.com/en/articles/9213685
- [S5] OpenAI Help (FR), "Analyse de données" (Switch to interactive or static chart): https://help.openai.com/fr-fr/articles/8437071 ; OpenAI blog, "Improvements to data analysis" (May 2024): https://openai.com/index/improvements-to-data-analysis-in-chatgpt/
- [S6] OpenAI Help, "What is the canvas feature" (versions, Show changes, code review); release notes (canvas removed for GPT-5.5): https://help.openai.com/en/articles/9930697 , https://help.openai.com/en/articles/6825453
- [S7] Claude palette write-ups: NousResearch hermes-agent `templates/claude.md`, https://github.com/NousResearch/hermes-agent/blob/HEAD/skills/creative/popular-web-designs/templates/claude.md
- [S8] Meliwat awesome-ios-design-md, Claude DESIGN.md (Tiempos prose, Styrene UI, `#D97757`, warm shadows): https://github.com/Meliwat/awesome-ios-design-md/blob/main/design-md/misc/claude/DESIGN.md
- [S9] level06 "Design System Preview: Claude" (text greys, body 16/1.6, serif 17/1.6, caption 14/1.43): https://design.level06.com/claude/preview
- [S10] Claude Help, "What are artifacts and how do I use them?" (panel beside chat, code, copy and download controls, Export, legacy cutoff 16 Sep 2026, Artifacts tab): https://support.claude.com/en/articles/17153992
- [S11] Claude Code docs, "Share session output as artifacts" (each publish is a version): https://code.claude.com/docs/en/artifacts
- [S12] ZeroLabs, "How to stream thinking blocks to user interfaces" (collapsible thinking accordion pattern): https://labs.zeroshot.studio/resources/how-to-stream-thinking-blocks-to-user-interfaces
- [S13] vercel/chatbot LICENSE (Apache-2.0) and `lib/artifacts/server.ts` (artifact kinds text, code, sheet, plus an image folder): https://github.com/vercel/chatbot
- [S14] Streamdown on npm (v2.5.0, Apache-2.0): https://registry.npmjs.org/streamdown
- [S15] Open WebUI License docs: https://docs.openwebui.com/license
- [S16] LobeHub lobe-chat LICENSE: https://github.com/lobehub/lobe-chat/blob/main/LICENSE
- [O] `/workspace/cc_web/ref/*.png`: ChatGPT logged-out home, answer with heading, bullets and table, table hover, code block, CSV block, bar-chart fallback; Claude login wall and two public artifact pages (document, interactive app). All 1024 px wide, captured 2026-10-25.
- Licences stated from package knowledge, not re-fetched today (verify in `package.json` at install): assistant-ui MIT, LibreChat MIT, react-markdown, remark-gfm and rehype-* MIT, KaTeX MIT, Shiki MIT, TanStack MIT, Papa Parse MIT, Vega and Vega-Lite BSD-3, yet-another-react-lightbox MIT, react-resizable-panels MIT, Radix and shadcn MIT, Tailwind MIT, Lucide ISC, Zustand MIT, zod MIT, jsdiff BSD-3, AI SDK Apache-2.0, Inter, Source Serif 4 and JetBrains Mono OFL-1.1.

## 10. Open questions
1. Next.js or a Vite SPA served by the Python backend? It decides how much of the vercel template stays.
2. Which transcript design wins (bc-af8d3012)? The default storage mode in §6.4 follows from it.
3. Is any user role allowed to see unmasked PHI in the UI at all, or is the UI de-identified-only for v1? A de-identified-only v1 would greatly simplify §6.6–6.7.
4. Logged-in screenshots of ChatGPT (table, chart, code) and Claude (artifact panel open) are still needed in `ref/` to replace the [E] values.
