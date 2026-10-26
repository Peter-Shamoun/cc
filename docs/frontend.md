# CC web chat (v1)

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

To point at a real backend, set `VITE_ANALYST_BASE`. Empty or unset means the offline mock; `/` means same origin (the Vite dev server proxies `/api` to `CC_API_URL`, default `http://127.0.0.1:8787`); anything else is an absolute base URL. The UI then uses `HttpAnalystAdapter`.

### Live round-trip against `api/` (v1)

```bash
pip install -e . && pip install -r api/requirements.txt
python -m api --port 8787                             # demo mode (no Server header): scripted model, synthetic DuckDB, no paid APIs
cd web
VITE_ANALYST_BASE=/ VITE_ANALYST_DEMO_TOKEN=demo:acme_labs:peter npx vite --port 5174
node scripts/shots_v1.mjs http://127.0.0.1:5174/ http://127.0.0.1:5175/ /workspace/cc_web/shots   # second URL = a mock-mode dev server
```

`VITE_ANALYST_DEMO_TOKEN` is only for the backend's demo bearer (`demo:<customer>:<user>`, synthetic data). Production leaves it unset and authenticates with the httpOnly `cc_session` cookie. The adapter:

- `POST /api/chat` with user turns only (`{conversationId, messages: [{id, role: 'user', text}]}`), reads the SSE stream, drops `heartbeat` events, remembers `messageId` from `message.start`.
- Stop: aborting the fetch also calls `POST /api/chat/{messageId}/stop` so the server drops the rest of the run.
- Download CSV: `GET /api/download/{csvFileId}` (opaque id, principal-bound, audited, 15 min). Falls back to client-side CSV of the preview only if that fails.
- The v1 Playwright run proves it: `POST /api/chat` and `GET /api/download/f_…` hit the FastAPI server, the CSV header was `payer_name,denial_rate,claims`, and the page logged no row data.

## v1 changes

- **Streamdown** (Vercel, Apache-2.0) replaces react-markdown for assistant prose: unfinished-markdown repair while streaming (an open `**`, half a table or code fence renders as if closed), per-block memoization, and its default rehype-harden/sanitize pipeline. We keep our own `code`/`pre`/`img`/`a`/`table` components (Shiki code blocks, no remote images). Notices: `web/THIRD_PARTY_NOTICES.md`; license texts ship in `node_modules/streamdown/LICENSE` and `node_modules/remend/LICENSE`.
- **Edit-and-branch**: the pencil under a user message edits it in place (Enter sends, Esc cancels, like ChatGPT/Claude). The old tail is kept as a version and `‹ 1/2 ›` arrows switch between versions. Branches live in memory with the thread (`TranscriptStore.replaceMessages`). Simplification: a branch nested inside a non-visible version is flattened when you switch away.
- **Mobile drawer**: below 768 px the sidebar is a modal drawer with a scrim; choosing a chat or New chat closes it. The header keeps sidebar + new-chat buttons, and `main` no longer forces 400 px min width.
- **Search and rename**: "Search chats" filters thread titles client-side (titles never leave the page); the pencil or a double-click renames inline (`TranscriptStore.renameConversation`).
- **Table virtualization**: tables over 200 rows render a TanStack Virtual window (spacer rows keep native `<table>` semantics, sticky header, sorting and filtering still work on all rows).
- Tests: 23 (was 18), adding the HTTP adapter contract, Streamdown repair, virtualization, store rename/replace and an edit-and-branch + feedback + search + shortcut flow.
- v1.2: a follow-up question is pinned to the top of the viewport and its answer streams in below (ChatGPT/Claude behavior) instead of the view chasing the bottom; the newest answer reserves most of a viewport for this. "Jump to latest" releases the pin.
- v1.1: thumbs-down "Tell us more" panel (reason chips + optional detail, kept in component state only until a PHI-safe feedback endpoint exists), Ctrl/⌘+K search chats, Ctrl/⌘+Shift+O new chat, single-series charts follow the palette (Paper = clay accent), usage line hidden for zero-query turns, inline favicon.

## Stack

Vite, React 18, TypeScript, Tailwind CSS v4, Streamdown (Apache-2.0, v1), Shiki, TanStack Table v8 + TanStack Virtual, vega-embed (Vega-Lite), lucide-react. Everything except Streamdown/remend is MIT, BSD or ISC.

The spec recommends forking vercel/chatbot (Apache-2.0) with Streamdown. For v0 we built a clean Vite app instead, because the fork (Next.js, auth, Postgres, Blob) was too heavy to strip and get building in the time box. **No code from vercel/chatbot, ChatGPT or Claude is included.** v1 depends on Streamdown as a package (not copied), and its notice is in `web/THIRD_PARTY_NOTICES.md`. If we later copy anything from vercel/chatbot, add its `LICENSE` and a `NOTICE` under `web/`. ChatGPT and Claude were observed only, from screenshots. No CSS, JS, fonts or icons were taken from them.

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

### HTTP errors before the stream starts

`httpError` (`web/src/adapters/http.ts`) resolves an error in this order: `error.code` if it's a known code, then `error.legacyCode`, then the HTTP status. The fallback by status is: 400/422 → validation, 401 → auth_expired, 403 → access_denied, 404 → not_found, 405 → method_not_allowed, 409 → conflict, 410 → transcripts_unavailable, 413 → payload_too_large, 429 → rate_limited.

| code | Card title | Retry |
|---|---|---|
| not_found | Not found | no |
| access_denied | Not available | no |
| payload_too_large | Message too long (gives the limit from `maxBytes`) | no |
| clarification_expired / conflict | Ask again | no |
| rate_limited | Too many requests (honours Retry-After) | yes |
| internal / any 5xx | Analyst unavailable (generic copy, never the server's message) | yes |
| method_not_allowed | Not supported | no |
| unknown 4xx | Request not accepted (keeps the raw code) | no |

Known 4xx cards show the server's fixed message, capped at 300 characters. The adapter handles the regenerate 404 and resume 409 fallbacks before `httpError` runs.

## Artifact panel

Opens from any table, chart, code or image (and artifact cards). It's resizable by dragging or with the arrow keys (double-click resets to 50%, min 360 px, max 75%). It has segmented tabs (Table | SQL, Chart | Spec | Data, Code, Preview), Copy, Download, Full screen and Close (Esc). Opening it collapses the sidebar on screens narrower than 1400 px.

## PHI rules implemented

- **No PHI in URLs.** The app has a single route. Prompts go in POST bodies. Downloads use short-lived `blob:` URLs revoked after 60 s.
- **No browser persistence of data.** Only `cc.pref.theme` and `cc.pref.palette` are written, through `prefs.ts` (allowlist plus a length cap). Transcripts sit behind `TranscriptStore`. The default is `ephemeral` (memory only), and the sidebar shows "History off".
- **No row data in console logs.** The app never logs. The screenshot script and the App test assert that no fixture row data shows up in console output and that storage holds only pref keys.
- **Masking.** Columns with `phi: true` render as `•••• 56`, can't be sorted, and are dropped from TSV copy, CSV export and message copy.
- **Rendering safety.** No raw HTML in markdown. Remote markdown images are replaced by their alt text. Links get `noopener noreferrer nofollow`. Vega specs lose any `data.url`.

## How close is this to vercel/chatbot, and what would a full fork take?

vercel/chatbot today (checked 2026-10-25 via the GitHub tree): Next.js 16 App Router, React 19, AI SDK 7 (`ai`, `@ai-sdk/react` `useChat`), Streamdown via its `components/ai-elements/*`, shadcn/ui, Auth.js (`next-auth` 5 beta) with guest users, Drizzle + Postgres for chats/messages/votes/documents, Vercel Blob for uploads, resumable streams (`api/chat/[id]/stream`).

| vercel/chatbot | CC `web/` | Match |
|---|---|---|
| `components/chat/messages.tsx`, `message.tsx`, `message-actions.tsx` | `App.tsx` thread + `components/Message.tsx` (copy, vote, regenerate) | Same split, same responsibilities |
| `components/chat/message-editor.tsx` (edit user message, then regenerate) | `UserBubble` inline editor | Same UX; we add `‹ n/m ›` branch versions, which the template does not keep |
| `components/chat/multimodal-input.tsx`, `submit-button.tsx`, `suggested-actions.tsx` | `Composer.tsx`, empty-state suggestion chips | Close (no file upload: PHI) |
| `components/chat/app-sidebar.tsx`, `sidebar-history.tsx`, `sidebar-history-item.tsx` | `Sidebar.tsx` (date buckets, rename, delete, search, mobile drawer) | Close; theirs is shadcn `Sidebar`, ours hand-rolled |
| `components/chat/artifact*.tsx`, `version-footer.tsx`, `artifacts/{code,sheet,image,text}` | `ArtifactPanel.tsx` (table/chart/SQL/image tabs, version label) | Same concept; theirs edits documents server-side with diffs, ours is read-only result views |
| `components/ai-elements/{tool,reasoning,code-block,shimmer}.tsx` | `parts/misc.tsx` (`ToolGroup`, `ReasoningView`), `parts/CodeBlock.tsx`, `.shimmer` | Same parts |
| Streamdown | Streamdown (v1) | Same library |
| `useChat` + AI SDK UI message stream protocol | `lib/useChat.ts` + spec §5 `StreamEvent` reducer over SSE | Different protocol: ours carries typed `table`/`chart`/`callout`/`file` parts with PHI flags |
| `app/(chat)/api/chat/route.ts` (model calls in Next) | FastAPI `api/` (analyzer, DuckDB, Cognito/STS) | Deliberately different: the backend must stay in Python next to the data path |
| Drizzle/Postgres history, votes, documents | `TranscriptStore` interface, `ephemeral` in memory | Different by design (no chat text at rest, see PR #50 Design A) |
| Auth.js, guest users, Blob uploads | httpOnly `cc_session` from Cognito, no uploads | Not applicable |

So the component structure is roughly 1:1 for the chat shell (thread, message, actions, editor, input, sidebar, artifact, tool/reasoning parts, Streamdown); the differences are below the UI.

**A full fork would take**, roughly 1.5–3 engineer-days:
1. Fork vercel/chatbot into `web/` with its `LICENSE` (Apache-2.0) and a `NOTICE`; mark modified files.
2. Delete the parts we cannot use: `app/(auth)` + `next-auth` (replace with the `cc_session` cookie check in middleware), Drizzle/Postgres + `lib/db` (replace with our `TranscriptStore`), Vercel Blob uploads, `api/document`, `api/suggestions`, weather demo, model selector and the AI Gateway/provider wiring.
3. Replace `app/(chat)/api/chat/route.ts` with a proxy to FastAPI, and either (a) make `api/` emit the AI SDK UI message stream (data parts `data-table`, `data-chart`, `data-callout` …) so the template's `useChat` works unchanged, or (b) keep our `StreamEvent` SSE and swap `useChat` for ours. (a) is the more "template-native" path; (b) keeps the spec §5 contract and is less work.
4. Port our part renderers (`DataTable`, `ChartView`, callouts, `FileCard`, PHI masking, CSV via `/api/download/{id}`) into `components/chat/message.tsx`, and our artifact kinds (table, chart, SQL) into `artifacts/`.
5. Re-theme shadcn tokens to our Neutral/Paper palettes, re-add the PHI rules (no row data in storage/console/URL; the template's `localStorage` input persistence and URL-based chat ids need review), CSP headers, and port the 23 tests to its Playwright setup.
6. Hosting moves from static files to a Node server (Next.js), which the HIPAA hosting plan has to cover.

Recommendation: keep the Vite app (structure already mirrors the template, smaller attack surface, static hosting) unless we want the template's server-side features (resumable streams, document artifacts with diffs). If we do, step 3(a) is the piece worth doing first, since it would also let `api/` serve any AI SDK client.

## Not done yet (next steps)

- KaTeX (Streamdown math plugin), explicit rehype-sanitize allowlist review.
- react-resizable-panels for the artifact panel, column resize and visibility, and cell-selection "Ask about selection".
- Nested branch trees (v1 keeps one level of versions per edited message), the regenerate strategy menu.
- Full-text chat search (v1 searches titles), and the full-screen artifact sheet below lg.
- Live tool steps: `api/` emits tool_call parts after the analyzer loop finishes, so the UI shows "Analyzing" until then.
- CSP and `Cache-Control: no-store` headers (server side), vega-interpreter for a CSP without `unsafe-eval`, self-hosted Inter and Source Serif 4 fonts, idle timeout, PHI unmask flow, and download confirmation for `limited` and `phi` sensitivity.
- An ESLint rule banning direct `localStorage` use outside `prefs.ts`, and axe-core checks.
- Shiki currently ships its full language bundle as lazy chunks. Switch to `shiki/core` with only sql, python, json and bash.
- Playwright visual comparisons against logged-in reference screenshots.
