# CC web chat (v0)

A React chat UI for the CC analyst under `web/`. It follows the research spec in [`frontend-spec.md`](frontend-spec.md): ChatGPT's layout and density, plus Claude's artifact side panel. It runs fully offline against a mock adapter that replays synthetic fixtures. There are no network calls and no paid APIs.

## Run

```bash
cd web
npm install
npm run dev        # http://127.0.0.1:5173, mock adapter
npm test           # vitest (reducer, CSV/masking, adapters, store, prefs, App smoke test)
npm run build      # tsc + vite build
npm run shots      # Playwright screenshots of the running dev server -> ../../shots (needs Chrome; set CHROME_PATH)
```

To point at a real backend, set `VITE_ANALYST_BASE=http://127.0.0.1:8000` (any non-empty value). The UI then uses `HttpAnalystAdapter`. Empty or unset means the mock.

## Stack

Vite, React 18, TypeScript, Tailwind CSS v4, react-markdown + remark-gfm, Shiki, TanStack Table v8, vega-embed (Vega-Lite), lucide-react. All are MIT, BSD or ISC licensed.

The spec recommends forking vercel/chatbot (Apache-2.0) with Streamdown. For v0 we built a clean Vite app instead, because the fork (Next.js, auth, Postgres, Blob) was too heavy to strip and get building in the time box. **No code from vercel/chatbot, ChatGPT or Claude is included**, so no third-party notice is needed yet. If we later copy anything from vercel/chatbot, add its `LICENSE` and a `NOTICE` under `web/`. ChatGPT and Claude were observed only, from screenshots. No CSS, JS, fonts or icons were taken from them.

## Layout

| Path | What |
|---|---|
| `web/src/lib/contract.ts` | The typed stream contract (spec §5): `Part` union, `StreamEvent` union, persisted message shapes, and the pure `reduce()` that applies events to an assistant message |
| `web/src/adapters/types.ts` | `AnalystAdapter` (stream a turn, fetch a blob by opaque id) and `TranscriptStore` (spec §6.4) |
| `web/src/adapters/mock.ts` | `MockAnalystAdapter`: replays fixtures as SSE-shaped events with token-level streaming |
| `web/src/adapters/http.ts` | `HttpAnalystAdapter`: `POST /api/chat` (SSE) and `GET /api/blobs/:id`; includes an SSE parser |
| `web/src/adapters/memoryStore.ts` | `ephemeral` transcript store (memory only) |
| `web/src/fixtures/index.ts` | Four synthetic conversations: denial rate by payer, monthly trend chart plus a plotted image, CARC codes with a PHI-masked table and an artifact, and a blocked-query error |
| `web/src/components/` | Shell (`Sidebar`, `Composer`, `Message`, `ArtifactPanel`) and part renderers in `parts/` |
| `web/src/lib/prefs.ts` | The only module that touches `localStorage`; allowlisted UI prefs |
| `web/src/lib/theme.tsx`, `web/src/index.css` | Two palettes (`neutral` ChatGPT-like default, `paper` Claude-like) times light/dark, as CSS variables from spec §2 |
| `web/scripts/screenshots.mjs` | Playwright screenshot run, plus a PHI check (storage keys, console lines) |

## Stream contract

Transport is `POST /api/chat` returning `text/event-stream`, one JSON `StreamEvent` per `data:` line. Text arrives as `text.start` / `text.delta` / `text.end`, reasoning as `reasoning.delta` / `reasoning.end`, and everything else as a whole `part` (replaced by id) or a `part.patch`. `status` sets a transient progress line, and `message.end` closes the turn with a finish reason and usage.

Part types and how they render:

| Part | Renders as |
|---|---|
| `text` | Markdown (GFM) with a pulsing dot caret while streaming. Raw HTML and remote images are not rendered |
| `reasoning` | "Thinking…" shimmer, then a collapsed "Thought for N s ▸" |
| `tool_call` | Rows with tool icon, label, state (spinner, ✓, ✕), rows and ms; more than 2 consecutive calls group into "Used N tools ▸". Expanding shows the input SQL or args, never result rows |
| `table` | Sortable TanStack table, sticky header, numeric right-aligned, "Showing n of N rows", provenance footer. Hover toolbar: copy as TSV, download CSV (server `csvFileId` first, client CSV fallback), expand to panel. PHI columns are masked and excluded from copy and CSV |
| `file` | File chip with type icon, rows and size, download via adapter blob; previews the matching table in the panel |
| `image` | Fetched by opaque `blobId` into a revoked object URL; fixed aspect ratio skeleton; lightbox; download |
| `chart` | Vega-Lite via vega-embed (SVG renderer), themed per palette; download PNG, SVG and data CSV; expand. `data.url` is stripped and only inline `data.values` are used |
| `code` | ChatGPT-style headerless block (label top-left, copy top-right), Shiki highlighting, download, open in panel. `role: "query"` collapses under "SQL ▸ 1 query" |
| `callout` | Left accent bar; variants `assumption`, `caveat`, `data_quality`, `phi_redaction`, `info`; editable assumptions get "Change", which prefills the composer |
| `artifact` | Claude-style card that opens the side panel |
| `error` | Danger bar, message, collapsible details, Retry |
| unknown | Muted "Unsupported content" chip |

## Artifact panel

Opens from any table, chart, code or image (and artifact cards). It's resizable by dragging or with the arrow keys (double-click resets to 50%, min 360 px, max 75%). It has segmented tabs (Table | SQL, Chart | Spec | Data, Code, Preview), Copy, Download, Full screen and Close (Esc). Opening it collapses the sidebar on screens narrower than 1400 px.

## PHI rules implemented in v0

- **No PHI in URLs.** The app has a single route. Prompts go in POST bodies. Downloads use short-lived `blob:` URLs revoked after 60 s.
- **No browser persistence of data.** Only `cc.pref.theme` and `cc.pref.palette` are written, through `prefs.ts` (allowlist plus a length cap). Transcripts sit behind `TranscriptStore`. The default is `ephemeral` (memory only), and the sidebar shows "History off".
- **No row data in console logs.** The app never logs. The screenshot script and the App test assert that no fixture row data shows up in console output and that storage holds only pref keys.
- **Masking.** Columns with `phi: true` render as `•••• 56`, can't be sorted, and are dropped from TSV copy, CSV export and message copy.
- **Rendering safety.** No raw HTML in markdown. Remote markdown images are replaced by their alt text. Links get `noopener noreferrer nofollow`. Vega specs lose any `data.url`.

## Not done yet (next steps)

- Streamdown (or a remend-style repair) for unterminated markdown mid-stream; KaTeX; rehype-sanitize explicitly.
- Fork vercel/chatbot pieces if wanted (version picker and diff), react-resizable-panels, TanStack Virtual for big tables, column resize and visibility, and cell-selection "Ask about selection".
- Edit-and-branch for user messages (Edit currently prefills the composer), the regenerate strategy menu, and the thumbs-down feedback popover.
- Search chats, rename, ⌘K, mobile drawer sidebar, and the full-screen sheet below lg.
- CSP and `Cache-Control: no-store` headers (server side), vega-interpreter for a CSP without `unsafe-eval`, self-hosted Inter and Source Serif 4 fonts, idle timeout, PHI unmask flow, and download confirmation for `limited` and `phi` sensitivity.
- An ESLint rule banning direct `localStorage` use outside `prefs.ts`, and axe-core checks.
- Shiki currently ships its full language bundle as lazy chunks. Switch to `shiki/core` with only sql, python, json and bash.
- Playwright visual comparisons against logged-in reference screenshots.
