# ClaimClarity · Frontend interaction layer & UI states

| Field | Value |
|---|---|
| Owner | _TODO: name / team_ |
| Status | _TODO: Draft / In review / Approved_ |
| Last reviewed | _TODO: YYYY-MM-DD_ |
| Reviewers | _TODO: app team, QA, security, design_ |
| Code | `web/` (React 18 + TypeScript + Vite), contract `web/src/contract/v1.gen.ts` (contract **1.24.0**), API contract `api/CONTRACT.md` |
| Screenshot source | Local run of this repo (see §2), `images/frontend/*.png` |
| Related pages | [ClaimClarity · Data backend architecture](claimclarity-backend-architecture.md) · _TODO: link to API contract page_ · _TODO: QA / e2e runbook_ · _TODO: ADRs_ |

> **Note:** Every screenshot on this page was taken from a **local** copy of the web app (demo API with the scripted model, or the offline mock adapter) on synthetic data. The deployed frontend runs elsewhere and was not reachable. Filenames are stable (`images/frontend/<nn>-<slug>.png`); replace the files in place with captures from the deployed app when you can.

> **Note:** Images named `*-annotated.png` carry numbered red callouts. Each has a legend table right under it. The same file without `-annotated` is the clean capture.

> **Note:** Lines like `![TODO screenshot: …](images/frontend/placeholder-….png)` mark states that exist in code but could not be rendered locally. Each one says how to trigger it.

## Contents

_TODO: insert Confluence Table of Contents macro here (levels 2–3)._

---

## 1. Purpose and audience

This page maps what a ClaimClarity user sees and does in the web app: every page state, every interaction, and how the analyst agent's work shows up on screen. It is the companion to the [data backend architecture page](claimclarity-backend-architecture.md), which covers the AWS side (SFTP intake, storage, sign-in, the query path, the analyzer).

- **Purpose:** _TODO: what this page supports (UX review, QA planning, support triage, onboarding, audit evidence that PHI rules hold in the UI)._
- **Audience:** _TODO: frontend and API engineers, QA, product/design, support, security reviewers._
- **In scope:** _TODO: app shell, sidebar/history, composer, answer rendering, step timeline, artifact panel, errors and recovery, responsive/touch, accessibility, the frontend↔API contract as the UI uses it._
- **Out of scope:** _TODO: AWS infrastructure and data isolation (backend page), analyzer internals beyond what the user sees, deployment (see `docs/frontend_deploy.md`)._

## 2. How these screenshots were made

_TODO: one paragraph on why local captures stand in for the deployed app, and who should replace them._

| Mode | How it was started | What it gives |
|---|---|---|
| Demo API (HTTP adapter) | `CC_API_MODE=demo CC_API_LLM=scripted CC_API_MODEL_DELAY_SECONDS=0.6 CC_API_STREAM_DELAY_SECONDS=0.03 python -m uvicorn api.app:app --port 8140`, then `CC_API_URL=http://127.0.0.1:8140 VITE_ANALYST_BASE=/ VITE_ANALYST_DEMO_TOKEN=demo:acme_labs:peter npx vite --port 5174` | Real `POST /api/chat` SSE streams from the real `Analyzer` loop with `ScriptedLLM` on synthetic DuckDB data. Demo scenarios (`ChatRequest.scenario`, CONTRACT.md §9) were forced by rewriting the request body in Playwright. |
| Mock adapter (offline) | `npx vite --port 5173` with `VITE_ANALYST_BASE` unset | `MockAnalystAdapter` replays `web/src/fixtures/*` (charts with an image, PHI-masked table, artifact card, guard rejection, candidates, clarify, abstain). Memory transcript store, so the temporary-chat toggle is available. |
| Fault injection | Playwright `page.route('**/api/chat', …)` | HTTP 4xx/5xx bodies, 429 + `Retry-After`, refused connections, dropped streams, unknown part types, canned markdown. Same technique as `web/e2e/helpers.ts` (`injectStream`). |

- Browser: Chromium via Playwright, 1440×900 desktop (1000 px tall for some), 390×844 touch (mobile), 820×1000 (tablet). Light theme, Neutral palette unless the name says otherwise.
- Annotations are an SVG overlay drawn over the real element boxes just before the capture.
- Scripts were throwaway and are not in the repo. _TODO: decide whether to add a capture script under `web/scripts/` (there is already `npm run shots` → `scripts/screenshots.mjs`)._

> **Note:** The demo server keeps conversations in memory for the whole run, so later sidebar captures show many threads created by earlier captures ("Stop while planning", "Long query", …). That is expected.

---

## 3. App shell anatomy

The shell is `web/src/shell/AppShell.tsx`: sidebar (or collapsed rail), header, thread, composer, and the artifact panel on the right when open. Routes are path-based for status pages and hash-based for chats (`#/c/<opaque id>`, never titles or text).

![First run, empty chat: sidebar with New chat and Search chats, History off note, Demo user account row; header with CC Analyst, De-identified badge and More options; centred heading "What should we look into?", composer and four suggestion chips](images/frontend/01-empty-first-run-annotated.png)

_TODO: caption — "Empty chat, first run (desktop)."_

| # | Element | Component | Behaviour |
|---|---|---|---|
| 1 | New chat · Search chats | `components/Sidebar.tsx` (`nav[aria-label="Chat actions"]`) | New chat (`Ctrl/⌘ Shift O`); Search chats opens the full-text search dialog (`Ctrl/⌘ K`). |
| 2 | History note | `Sidebar` `data-testid="history-note"` | "History off · chats live in memory only" when `TranscriptStore.mode` is `ephemeral`; "History on" otherwise. |
| 3 | Account row | `Sidebar` `data-testid="account-button"` | Opens Settings / Keyboard shortcuts / Sign out (Sign out only when a session exists). Identity is a synthetic constant (`ACCOUNT`). |
| 4 | Close sidebar | `Sidebar` | Collapses to the 52 px rail (`Ctrl/⌘ Shift S`); preference stored as `cc.pref.sidebarCollapsed`. |
| 5 | Product name | `shell/Header.tsx` | Plain text "CC Analyst". No model or data-source picker (contract 1.24.0). |
| 6 | PHI mode badge | `Header` `data-testid="phi-badge"` | "De-identified", set by the server. _TODO: confirm which server field drives it; today it is static text._ |
| 7 | More options | `Header` | Dark/Light mode, Settings, Keyboard shortcuts. |
| 8 | Empty-state heading | `AppShell` | "What should we look into?" Only on an empty chat. |
| 9 | Composer | `components/Composer.tsx` | Auto-growing textarea, placeholder "Ask about your claims" (empty chat) / "Ask a follow-up" / "Reply…" (open clarification). |
| 10 | Attach text files | `Composer` | `.txt .csv .tsv .md .sql .json .log`, ≤ 256 KB each, read locally into chips; never uploaded as files. |
| 11 | Send | `Composer` | Disabled until there is text or a chip, and while offline. Turns into Stop while a turn streams. |
| 12 | Suggestion chips | `shell/StarterCards.tsx` | Four starters whose prompts match demo fixtures. Phones show a swipeable row of two-line cards above the composer. |

### 3.1 Layout regions and breakpoints

| Region | Desktop (≥ 768 px) | Phone (< 768 px) | Notes |
|---|---|---|---|
| Sidebar | 260 px column, collapsible to rail | Modal drawer (`role="dialog"`) with scrim, `min(300px, 85vw)` | §6, §14 |
| Header | 52 px, title menu when a chat is open | Same, title hidden under 520 px container width | §6.7 |
| Thread | Max ~48 rem column, virtualized rows | Full width | `shell/ThreadView.tsx` |
| Composer | Pinned at the bottom, footer "CC can make mistakes. Check figures against source before use." | Same | §7 |
| Artifact panel | Right side, resizable 360 px … 75 %, collapses the sidebar under 1400 px | Full-screen sheet | §11 |

_TODO: add a wireframe or diagram of the regions if useful._

---

## 4. User journeys end to end

_TODO: one-paragraph intro: the journeys below are the paths QA and support should know by heart._

### 4.1 J1 · First question to finished answer

1. Empty chat → type or pick a starter (`01`, `02`).
2. Send → user bubble appears, the thread gets a placeholder title, a collapsed **Thinking** row and Stop button show at once (`05`).
3. Steps stream: the activity line names the current step ("Checking the claims view", "Running query") (`06`); expanding shows each step (`07`).
4. The answer streams in below the steps with a caret (`08`), then tables, SQL, charts, callouts.
5. `message.end` → action row, Sources chip, usage line, follow-ups; the sidebar title becomes the server's PHI-free title (`10`, `12`).

![Finished answer: header title "Denial rate by payer", thread in sidebar, question bubble, "Thought for 1.8s" row, prose, table with provenance footer, View SQL, bar chart, composer](images/frontend/10-answer-complete-annotated.png)

| # | Element | Component | Behaviour |
|---|---|---|---|
| 1 | Chat title | `Header` | Server title from `message.end.conversationTitle` (derived from column names, never the question). Click → title menu; double-click → rename. |
| 2 | Active thread | `Sidebar` thread item | Highlighted; "…" menu on hover/focus. |
| 3 | Question bubble | `UserBubble` in `components/Message.tsx` | Hover shows time, Copy, Edit. |
| 4 | Step timeline header | `components/answer/StepTimeline.tsx` | Collapsed "Thought for N s"; click to expand (§8). |
| 5 | Answer prose | `components/parts/Markdown.tsx` (Streamdown) | Streams with unfinished-markdown repair. |
| 6 | Result table | `components/parts/DataTable.tsx` | Sortable, numeric right-aligned, provenance footer "from sem.claims · Showing 7 of 7 rows · 1.83 s". |
| 7 | View SQL | `TableSql` in `Message.tsx` | The query that produced this table, linked by `sqlPartId`. |
| 8 | Chart | `components/parts/ChartView.tsx` | Vega-Lite, only inline data. |
| 9 | Composer | `Composer` | Placeholder switches to "Ask a follow-up". |

Full-page capture of the same answer: `images/frontend/11-answer-complete-full.png`.

### 4.2 J2 · Follow-up, edit, regenerate

- Follow-up: the new question is pinned to the top of the viewport and its answer streams below; "Jump to latest" appears when the user scrolls away (`123`).
- Edit: pencil under the question → inline editor → Send creates a sibling version; `‹ 2/2 ›` switches (`118`, `119`).
- Regenerate: one click re-runs as the next version of the answer; the chevron offers Try again / More concise / More detailed / Ask to change response… (`20`, `120`–`122`).

### 4.3 J3 · Clarify, then answer

The agent asks before running anything when the question is ambiguous (`finishReason: needs_input`). The composer placeholder becomes "Reply…"; a chip or free text resumes the run (`ChatRequest.resume`) (`38`, `39`, `76`, `77`).

### 4.4 J4 · The agent can't or won't answer

Abstain (data not available, forecast, unsupported) shows a "Cannot answer" note with suggestions; requests for patient identifiers are refused (`33`, `34`, `44`, `78`).

### 4.5 J5 · Something fails, user retries

Error line with Retry in the answer column; transport problems (429, offline, reconnecting) show as a status line while the turn is still alive (§13) (`80`–`91`).

### 4.6 J6 · Come back to an earlier chat

Sidebar (date buckets, pins, filter at ≥ 20 chats) or Search chats (full text, server-side) → thread skeleton while the conversation loads → stored steps are replayed through the same reducer as a live stream (`100`–`117`).

### 4.7 J7 · Phone

Drawer sidebar, swipeable suggestions, artifacts as a full-screen sheet, action rows always visible (no hover) (`130`–`134`).

_TODO: add the journeys support sees most (e.g. "the numbers look wrong" → thumbs down → SQL inspector)._

---

## 5. State inventory

One row per UI state. "Screenshot" points at the image on this page (`P-…` = placeholder). Test files are relative to `web/src/` (unit, Vitest) or `web/e2e/` (Playwright).

| ID | State | Trigger | What the user sees | Component(s) | Tests | Screenshot |
|---|---|---|---|---|---|---|
| S01 | Empty chat, first run | Load `/`, no chat open | Heading, composer, 4 suggestion chips | `AppShell`, `StarterCards` | `shell/shell.test.tsx`, `e2e/mock.spec.ts` | 01 |
| S02 | Composer typing | Text in the box | Send enabled (filled), box grows to 240 px | `Composer` | `components/Composer.test.tsx` | 02 |
| S03 | Slash command menu | Type `/` | Listbox of 12 commands, arrow keys / Tab / Enter | `Composer`, `shell/slash.ts` | `Composer.test.tsx` | 03 |
| S04 | Attachment chips | Paste ≥ 1200 chars or > 15 lines; attach text files | Chips "PASTED" / file extension, remove ×, Backspace removes last | `Composer` | `Composer.test.tsx` | 04 |
| S05 | Attachment rejected | Non-text file or > 256 KB | Red notice "x isn't a text file · y is over 256 KB" | `Composer` (`attach-notice`) | `Composer.test.tsx` | P-attach-rejected |
| S06 | Drag-over | Drag files onto the composer | Focus ring around the composer | `Composer` (`data-dragging`) | `Composer.test.tsx` | P-attach-drag |
| S07 | Sending / Thinking | Send, before the first event | Question bubble, collapsed "Thinking" row, Stop button | `StepTimeline`, `Composer` | `components/answer/thinkingRow.test.tsx`, `e2e/visual-states.spec.ts` | 05 |
| S08 | Live step line | Step events arriving | One muted line naming the current step, no spinner/timer | `StepTimeline` | `components/answer/thinkingDisclosure.test.tsx`, `quietStream.test.tsx` | 06 |
| S09 | Live steps expanded | Click the live header | Rows turning running → done, running dot, escalation note, table skeleton | `StepTimeline`, `TableSkeleton` | `answer/answer.test.tsx`, `e2e/answer-edge.spec.ts` | 07 |
| S10 | Text streaming | `text.delta` | Paced prose with a caret | `Markdown`, `lib/pacedText.ts` | `lib/pacedText.test.tsx`, `answer/liveTurnPacing.test.tsx`, `parts/streaming.test.tsx` | 08 |
| S11 | Answer complete | `message.end` `stop` | Prose, table, SQL, chart, callouts, Sources, action row, usage | `AssistantTurn` | `v1.test.tsx`, `e2e/contract-outcomes.spec.ts` | 10, 12 |
| S12 | Steps expanded (done) | Click "Thought for…" | Counts, reasoning summary, each step with detail | `StepTimeline` | `answer/thinkingDisclosure.test.tsx` | 13, 14 |
| S13 | Answer details popover | Click usage line | Tokens, cost (est.), queries, first token, total time, stage bar | `answer/AnswerMeta.tsx` | `answer/answer.test.tsx` | 16 |
| S14 | SQL inspector | "SQL" in action row | Tabs per query, SQL, row count, columns | `answer/SqlInspector.tsx` | `answer/sqlMasking.test.tsx` | 17 |
| S15 | Sources popover | "Looked at …" chip | Views used, columns, "No row values are shown" | `answer/Sources.tsx` | `answer/sources.test.tsx`, `sourcesConsulted.test.tsx` | 18 |
| S16 | More actions menu | "…" in action row | Branch in new chat, Print this answer, Copy as Markdown, Copy SQL | `AssistantTurn` | `answer/messageActions.test.tsx`, `shell/branch.test.tsx` | 19 |
| S17 | Regenerate menu | Chevron next to Regenerate | Try again, More concise, More detailed, Ask to change response… | `AssistantTurn` | `contract/regenerate.test.ts`, `messageActions.test.tsx` | 20 |
| S18 | Feedback panel | Thumbs down | "Tell us more" reason chips + comment ≤ 1000 chars | `FeedbackPanel` | `lib/feedbackWire.test.ts`, `contract/feedback.test.ts` | 21 |
| S19 | Repair | Query fails (binder error) | Failed step ✕ red, "Repaired the query", re-run attempt 2 | `StepTimeline` | `answer/edgeScenarios.test.tsx` | 30, 43 |
| S20 | Escalation | Small model gives up | "Handed off to Sonnet 5.5", "Escalated to Sonnet 5.5 · repairs ran out" | `EscalationNote`, `StepTimeline` | `answer/answer.test.tsx` | 31, 09 |
| S21 | Candidates | Multi-candidate run | "Compared 3 candidates · execution agreement" | `StepTimeline` | `answer/comparisonPass.test.tsx` | 32, 75 |
| S22 | Abstain | `finishReason: abstain` | "The data needed for this isn't available", Try asking chips | `answer/States.tsx` `AbstainCard` | `answer/answer.test.tsx` | 33, 78 |
| S23 | PHI refusal | Ask for patient identifiers | "I can't share patient-identifying details" + alternatives | `AbstainCard` | `security/phi.test.tsx` | 34 |
| S24 | Analysis error | Run fails server-side | "Something went wrong / The analysis failed. Try again." + Retry | `ErrorView` (`parts/misc.tsx`) | `answer/malformedParts.test.tsx` | 35 |
| S25 | Chart answer | Time series / ≤ 50-row categorical | Line or bar chart under the table | `ChartView` | `parts/chartActions.test.tsx`, `render/chartEdge.test.ts` | 36, 66 |
| S26 | Report artifact | "report" questions | Artifact card + panel opens on its own | `ArtifactCard`, `ArtifactPanel` | `components/artifacts.test.tsx`, `parts/artifactKind.test.tsx` | 37 |
| S27 | Clarify (single) | `finishReason: needs_input` | Question, choice chips, "Or type your answer below", composer "Reply…" | `ClarifyCard` | `contract/clarify.test.ts`, `shell/clarifyReply.test.tsx` | 38, 76 |
| S28 | Clarify (multi-question form) | `ClarificationPart.questions` | Tabs (Metric / Period / Group by), options, Other…, Skip | `answer/ClarifyForm.tsx` | `answer/clarifyForm.test.tsx` | 39 |
| S29 | Clarify answered + resumed | Pick a chip | Chip filled, "You answered … Continuing…", new answer | `ClarifyCard` | `e2e/shell-clarify-reply.spec.ts` | 77 |
| S30 | Nested clarify | Second question after resume | Another clarification card | `ClarifyCard` | `contract/edgeCases.test.ts` | 45 |
| S31 | Empty result | Query returns 0 rows | Explanation, table header with "Query returned 0 rows", caveat | `DataTable` (`table-empty`) | `parts/tableEdge.test.tsx` | 40 |
| S32 | Multiple tables | Two result sets | Two titled tables, each with its own SQL and chart | `AssistantTurn` | `contract/edgeCases.test.ts` | 41 |
| S33 | Long SQL / wide table | 100+ line query, many columns | Horizontal scroll inside the table, edge fades | `CodeBlock`, `DataTable` | `parts/codeChrome.test.tsx`, `parts/tableEdge.test.tsx` | 42, 142 |
| S34 | Stopped (planning) | Stop before any SQL | "Stopped · Retry" | `StoppedNote` | `lib/useChat.races.test.tsx` | 46 |
| S35 | Stopped (mid-query / repair / candidates / escalation / render) | Stop at that stage | Steps closed as "stopped", "Stopped" note, action row | `StepTimeline`, `StoppedNote` | `contract/edgeCases.test.ts`, `e2e/chaos.spec.ts` | 47–52 |
| S36 | Table hover toolbar | Hover a table | Copy table, Download CSV, Expand | `DataTable` | `parts/tableEdge.test.tsx` | 60 |
| S37 | Table sorted | Click a header | Arrow on the sorted column | `DataTable` | `parts/polish.test.tsx` | 61 |
| S38 | Column resized | Drag the column edge | Wider column | `DataTable` (`col-resize`) | `parts/tableResize.test.tsx` | 62 |
| S39 | File card | Result > 50 rows or CSV attached | CSV chip with rows and size; download via opaque id | `FileCard` | `lib/csvFilename.test.ts`, `security/csvInjection.test.ts` | 59 |
| S40 | Artifact panel · table | Expand table | Table/SQL tabs, Copy, Download, Full screen, Close, Filter rows | `ArtifactPanel` | `components/ArtifactPanel.test.tsx` | 63, 64 |
| S41 | Artifact panel resized | Drag or arrow keys on the separator | Narrower/wider panel | `ArtifactPanel` | `ArtifactPanel.test.tsx` | 65 |
| S42 | Chart point picked | Click a mark | "Ask about Northwind Health Plan" chip | `ChartView` (`chart-ask`) | `parts/chartActions.test.tsx` | 67 |
| S43 | Artifact panel · chart | Expand chart | Chart / Spec / Data tabs | `ArtifactPanel` | `ArtifactPanel.test.tsx` | 68, 69 |
| S44 | Image part + caveat | Analyst-rendered figure | Image with caption, Caveat callout | `ImageView`, `Callout` | `render/render.test.tsx` | 70 |
| S45 | PHI-masked column | `Column.phi: true` | "•••• 56", shield icon, PHI masked callout | `DataTable`, `Callout` | `security/phi.test.tsx`, `answer/dataWithheld.test.tsx` | 71, 72 |
| S46 | Artifact panel · code | Artifact card (code) | SQL in the panel | `ArtifactPanel` | `artifacts.test.tsx` | 73 |
| S47 | Guard rejection | Query names a relation outside `sem.*` | Failed step + "Access denied … I can answer this from the sem.* semantic views instead" + Retry/Details | `ErrorView`, `StepTimeline` | `adapters/http.guard.test.ts` _(TODO: confirm; that file tests the stream guard)_, `e2e/mock.spec.ts` | 74 |
| S48 | HTTP 5xx | `POST /api/chat` 500 | "Analyst unavailable · The analyst returned 500" + Retry | `httpError` → `ErrorView` | `adapters/http.httpError.test.ts`, `e2e/chaos.spec.ts` | 80 |
| S49 | 413 too large | Body > 1 MiB | "Message too long · Shorten the message to under 1024 KB" (no Retry) | `httpError` | `adapters/http.httpError.test.ts` | 81 |
| S50 | 422 validation | Bad body | "Couldn't send that" + server's fixed message | `httpError` | `adapters/http.errors.test.ts` | 82 |
| S51 | 409 clarification expired | Stale resume | "Ask again" | `httpError` | `adapters/http.errors.test.ts` | 83 |
| S52 | Network failure | Connection refused / DNS / CORS | "Can't reach the analyst" + Retry | `networkError()` | `adapters/http.errors.test.ts` | 84 |
| S53 | Retry recovered | Retry after S52 | Normal answer | `useChat` | `lib/useChat.resilience.test.tsx` | 85 |
| S54 | 429 countdown | 429 + Retry-After | "Busy right now, retrying in 8s…", auto-retry | `useChat`, `rateLimitLabel` | `adapters/http.ratelimit.test.ts`, `e2e/edge.spec.ts` | 86 |
| S55 | Transport announcement | 429 / offline / reconnecting | Screen-reader text (polite, throttled) | `TransportAnnouncer` | `components/transportAnnouncer.test.tsx`, `lib/transportAnnounce.test.ts` | 87 |
| S56 | Reconnecting | Stream drops mid-answer | "Reconnecting…" after 1 s quiet | `useChat` (`RECONNECT_QUIET_MS`) | `adapters/http.midstream.test.ts`, `lib/useChat.connection.test.tsx` | 88 |
| S57 | Connection lost | Reconnect budget used / replay gone | "Connection lost · Response interrupted" + Retry | `useChat`, `ErrorView` | `adapters/http.streamgone.test.ts` | 89 |
| S58 | Offline | Browser offline ≥ 1 s | Pill "You're offline. Sending resumes when you reconnect.", Send disabled, draft kept | `OfflineBanner`, `useSettledOnline` | `adapters/http.flapping.test.ts`, `e2e/edge.spec.ts` | 90 |
| S59 | Session expired (in chat) | 401 with a demo token | "Session expired · Sign in again to continue." | `httpError` | `auth/session.test.ts` | 91 |
| S60 | Signed out page | `/signed-out` | "You're signed out" + Sign in again | `StatusPage` | `shell/routes.test.tsx`, `e2e/shell-routes.spec.ts` | 92 |
| S61 | Session expired page | `/signed-out?reason=expired` (cookie session 401) | "Your session expired" + Sign in again | `StatusPage` | `auth/session.test.ts`, `auth/crossTabSignOut.test.ts` | 93 |
| S62 | Not found | Any other path | "Page not found" + Go to CC | `NotFoundPage` | `shell/routes.test.tsx` | 94 |
| S63 | Unsupported part | Unknown `part.type` | Muted chip "Unsupported content (sankey)", rest renders | `Unsupported` | `answer/malformedParts.test.tsx`, `contract/fuzz.test.ts` | 95 |
| S64 | Chart failed to load | Vega chunk fails | "Chart failed to render: …" inside the chart frame | `ChartView`, `lib/onceOk.ts` | `lib/idlePrefetch.test.ts`, `parts/chartSplit.test.tsx` | 96 |
| S65 | App crash | Render error escapes every section | "Something went wrong", Reload, Start a new chat, Reference id + diagnostics | `ErrorBoundary` (app scope) | `components/ErrorBoundary.test.tsx`, `lib/errorReporting.test.tsx` | 97 |
| S66 | Section crash | Render error inside a part / panel | "This part couldn't be displayed. Ref …" + Try again / Close | `ErrorBoundary` (section scope) | `components/sectionBoundaries.test.tsx` | P-section-crash |
| S67 | Stalled stream | No bytes for 45 s | Before start: "The analyst stopped responding" (retryable); mid-answer: reconnect | `guardBody` | `adapters/http.guard.test.ts` | P-stalled |
| S68 | Answer too large | One SSE line > 32 MiB | What arrived + "Answer too large to show" | `guardBody` | `adapters/http.guard.test.ts` | P-too-large |
| S69 | Sidebar list + filter | ≥ 20 chats | "Filter chats" box, Pinned / Today / Yesterday / Previous 7 days / Previous 30 days / month / year buckets | `Sidebar` | `components/sidebarFilter.test.tsx`, `e2e/shell-sidebar-filter.spec.ts` | 100, 101 |
| S70 | Filter, no match | Filter text matches nothing | "No chat titles match "…"." + "Search messages" link | `Sidebar` (`filter-empty`) | `sidebarFilter.test.tsx` | 102 |
| S71 | Thread options | "…" or right-click on a thread | Export as Markdown, Rename, Pin, Archive, Delete | `Sidebar` | `shell/archive.test.tsx`, `adapters/httpStore.pinArchive.test.ts` | 103 |
| S72 | Pinned | Pin | "Pinned" group at the top | `Sidebar` | `contract/pinArchive.test.ts` | 104 |
| S73 | Rename inline | Rename / double-click | Input in the row; Enter saves, Esc cancels | `Sidebar` | `shell/title.test.ts` | 105 |
| S74 | Delete / archive toast | Delete or Archive | Toast "Deleted "…"" with Undo (5 s) | `ToastProvider` | `shell/toast.test.tsx` | 107 |
| S75 | Search dialog | `Ctrl/⌘ K` | Search box, New chat, results with snippets (titles and message text) | `shell/SearchDialog.tsx` | `shell/SearchDialog.test.tsx`, `e2e/shell-find.spec.ts` | 108 |
| S76 | Sidebar rail | Collapse | 52 px rail: open, new chat, search, account | `SidebarRail` | `components/sidebarRail.test.tsx` | 109 |
| S77 | Account menu | Account row | Settings, Keyboard shortcuts, Sign out | `Sidebar` | `shell/shell.test.tsx` | 110 |
| S78 | Settings · General | `Ctrl/⌘ ,` | Theme (System/Light/Dark), Style (Neutral/Paper), Send with Enter | `shell/SettingsDialog.tsx` | `shell/settings.test.tsx` | 111 |
| S79 | Settings · Data controls | Tab | Chat history mode, Unsent drafts (memory only), Archived chats (manage/unarchive), Export chats | `SettingsDialog` | `shell/settings.test.tsx`, `shell/archive.test.tsx` | 112 |
| S80 | Keyboard shortcuts | `Ctrl/⌘ /` | Shortcut table (Chats / Navigation / Composer) | `ShortcutsDialog`, `shell/shortcuts.ts` | `e2e/keyboard-sr.spec.ts` | 113 |
| S81 | Header title menu | Click chat title | Rename, Copy as Markdown, Export as Markdown, Export as JSON (live API), Delete | `Header` | `shell/fidelity.test.ts` | 114 |
| S82 | Header more menu | "…" top right | Dark/Light mode, Settings, Keyboard shortcuts | `Header` | `shell/shell.test.tsx` | 115 |
| S83 | Opening a chat | Click a thread (HTTP store) | Thread skeleton after a short grace period | `AppShell` (`thread-skeleton`) | `shell/routes.test.tsx` | 116 |
| S84 | Response interrupted | Stored chat ends on a question (reload mid-answer) | "Response interrupted · This answer stopped before it finished." + Retry | `withInterrupted` in `lib/useChat.ts` | `lib/useChat.interrupted.test.tsx`, `e2e/answer-edge.spec.ts` | 117 |
| S85 | Edit question | Pencil under a question | Inline editor, Cancel / Send | `UserBubble` | `components/userBubble.test.tsx` | 118 |
| S86 | Question versions | Edited question | `‹ 2/2 ›` under the question | `VersionNav` | `lib/useChat.branches.test.tsx`, `lib/threadTree.test.ts` | 119 |
| S87 | Answer versions | Regenerate | `‹ 2/2 ›` in the action row | `VersionNav` | `useChat.branches.test.tsx` | 120 |
| S88 | Styled regeneration | More concise / More detailed | Version labelled "Concise" / "Detailed" | `AssistantTurn` (`regen-style`) | `contract/regenerate.test.ts` | 121 |
| S89 | Ask to change response | Menu item | Inline input "Ask to change response…" | `answer/ChangeResponse.tsx` | `answer/messageActions.test.tsx` | 122 |
| S90 | Jump to latest | Scroll up during/after an answer | Round ↓ button above the composer | `AppShell` (`jump-latest`) | `shell/turnSpacing.test.tsx` | 123 |
| S91 | Long question | > 700 chars or > 9 lines | Bubble clipped with fade + "Show more" | `UserText` | `components/userBubble.test.tsx` | 124 |
| S92 | Keyboard focus | Tab / Shift+Tab | Visible focus ring | all | `shell/focus.test.tsx`, `e2e/shell-focus.spec.ts`, `e2e/a11y.spec.ts` | 125 |
| S93 | Phone empty | < 768 px | Sidebar button, swipeable cards, composer | `AppShell`, `StarterCards` | `e2e/visual.spec.ts` (mobile) | 130 |
| S94 | Phone drawer | Open sidebar / edge swipe | Drawer + scrim | `Sidebar` (drawer), `shell/swipe.ts` | `components/sidebarTouch.test.tsx`, `e2e/shell-swipe.spec.ts` | 131 |
| S95 | Phone answer / sheet | Answer, expand table | Answer full width; panel as full-screen sheet | `ArtifactPanel` | `e2e/visual.spec.ts` | 132–134 |
| S96 | Tablet | 820 px | Sidebar + thread, chart below table | — | — | 135 |
| S97 | Themes | Settings / `/dark` / OS | Neutral or Paper × light or dark | `lib/theme.tsx` | `lib/themeSync.test.tsx` | 136–138 |
| S98 | Temporary chat | Toggle on an empty chat (memory store) | "Temporary chat" heading and note; not listed, searched or titled | `Header`, `shell/temporaryStore.ts` | `shell/temporary.test.tsx` | 139, 140 |
| S99 | Markdown / code | Prose with quote, table, list, link, fenced code | Rendered GFM, Shiki code block with language label and Copy | `Markdown`, `CodeBlock` | `render/render.test.tsx`, `parts/codeChrome.test.tsx` | 141 |
| S100 | Math (KaTeX) | `$…$` or `$$…$$` in prose | **Locally: whole-app crash** (see §21) | `Markdown` + `@streamdown/math` | `parts/math.test.tsx`, `parts/displayMath.test.tsx` (jsdom) | P-math |
| S101 | Large table | > 200 rows | Virtualized rows, sticky header, "Showing 60 of 600 rows · Open full table" | `DataTable` (TanStack Virtual) | `parts/tableEdge.test.tsx` | 142 |
| S102 | Image lightbox | Expand image | Full-screen dialog, Close | `ImageView` | `render/render.test.tsx` | P-lightbox |
| S103 | Branch in new chat | More actions → Branch | New chat with the thread up to that answer, toast "Branched into a new chat" | `AppShell` | `shell/branch.test.tsx` | P-branch |
| S104 | Print one answer | More actions → Print | Print view with question + answer only | `printOne`, `index.css` print rules | `printCss.test.ts`, `e2e/print.spec.ts` | P-print |
| S105 | Signed out in another tab | Sign out elsewhere | This tab leaves to the signed-out page | `auth/session.ts` (BroadcastChannel) | `auth/crossTabSignOut.test.ts` | P-cross-tab |
| S106 | Thumbs up / copied | Click thumbs up / Copy | Filled thumb; toast "Copied as Markdown" / "SQL copied" | `AssistantTurn` | `answer/actionRowFidelity.test.tsx` | _TODO_ |
| S107 | History gone (410) | Customer key destroyed | "Chat unavailable · This chat is no longer available." | `httpError` | `adapters/httpStore.test.ts` | P-410 |

_TODO: review the inventory with QA; add any state missing from the e2e suites._

---

## 6. Sidebar & history

_TODO: one paragraph: what the sidebar is for, where history lives (server `HttpTranscriptStore` with the live API, memory `TemporaryAwareStore` with the mock), and that titles never come from the question._

### 6.1 Thread list, buckets and filter

![Sidebar with Filter chats box, Today bucket and a long list of threads](images/frontend/100-sidebar-history-filter-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Filter chats | Appears only at ≥ 20 chats (`FILTER_AT`). Case- and accent-insensitive title match; Esc clears, ↓ moves into the list, Enter opens the first hit. Titles never leave the page. |
| 2 | Date bucket | Pinned, Today, Yesterday, Previous 7 days, Previous 30 days, then month (this year) or year. Calendar days, DST-safe (`bucket()`). |
| 3 | Thread item | One tab stop for the list; ↑ ↓ Home End move (roving focus). |

![Filtered list](images/frontend/101-sidebar-filter-results.png)

![Filter with no match: "Search messages" link](images/frontend/102-sidebar-filter-empty.png)

### 6.2 Thread actions

![Thread options menu: Export as Markdown, Rename, Pin, Archive, Delete](images/frontend/103-thread-options-menu-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Thread menu | Opens from "…" (hover/focus) or right-click. Pin/Archive appear only when the store supports them (`Capabilities.pinArchive`, contract 1.17.0). |

![Pinned group at the top](images/frontend/104-thread-pinned.png)

![Inline rename](images/frontend/105-thread-rename-inline.png)

![Toast with Undo after delete](images/frontend/107-toast-undo-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Toast | "Deleted "<title>"" + Undo for 5 s (`UNDO_MS`). Archive shows the same pattern; a server refusal shows "Couldn't archive that chat. Try again." |

_TODO: confirm whether delete is optimistic on the live API and what happens if the DELETE fails after Undo expires._

### 6.3 Search chats

![Search dialog with results](images/frontend/108-search-dialog-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Search box | Full-text over titles **and** message text. On the live API it is `POST /api/conversations/search` (POST so the text never sits in a URL). |
| 2 | Result | Opens the chat (and the matched message). Archived chats are included. |
| 3 | Match highlight | `search-mark` around the hit in the snippet. _(Not captured: the demo store returned title-only hits.)_ |

### 6.4 Collapsed rail and account

![Collapsed rail](images/frontend/109-sidebar-collapsed-rail.png)

![Account menu](images/frontend/110-account-menu.png)

### 6.5 Settings, archived chats, shortcuts

![Settings · General](images/frontend/111-settings-general.png)

![Settings · Data controls with archived chats listed](images/frontend/112-settings-data-controls-archived.png)

![Keyboard shortcuts dialog](images/frontend/113-shortcuts-dialog.png)

| Shortcut | Action (from `shell/shortcuts.ts`) |
|---|---|
| `Ctrl/⌘ Shift O` | New chat |
| `Ctrl/⌘ K` | Search chats |
| `Ctrl/⌘ Shift S` | Toggle sidebar |
| `Shift Esc` | Focus chat input |
| `Ctrl/⌘ Shift C` | Copy last response |
| `Ctrl/⌘ Shift E` | Export chat as Markdown |
| `Ctrl/⌘ ,` | Settings |
| `Ctrl/⌘ /` | Show shortcuts |
| `Esc` | Stop generating · close panel or dialog |
| `/` | Slash commands |
| `Shift Enter` | New line in message |
| `↑` in an empty composer | Edit the last question |

### 6.6 Opening a stored chat

![Thread skeleton while a conversation loads](images/frontend/116-thread-opening-skeleton.png)

![Stored chat that ends on a question: "Response interrupted" with Retry](images/frontend/117-response-interrupted-after-reload.png)

_TODO: explain the disconnect grace (`CC_API_DISCONNECT_GRACE_SECONDS`, 10 s) and why a reload mid-answer usually reattaches instead of showing S84._

### 6.7 Header title menu and more menu

![Chat title menu](images/frontend/114-header-chat-title-menu.png)

![More options menu](images/frontend/115-header-more-menu.png)

### 6.8 Temporary chat (memory store only)

![Temporary chat on: heading "Temporary chat" and note](images/frontend/139-temporary-chat-on-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Temporary toggle | Shown only with the memory store, and only on an empty chat or while temporary is on. |
| 2 | Note | "This chat won't appear in your history or search, and it's cleared when you leave it." |

![Temporary chat with an answer](images/frontend/140-temporary-chat-answer.png)

> **Note:** With the live API the toggle is hidden today (`tempStore` is null for `HttpTranscriptStore`) even though the server announces `Capabilities.temporaryChat`. _TODO: confirm whether that is intended._

---

## 7. Composer

_TODO: one paragraph: the composer is the only input; drafts are kept per chat in memory only (never `localStorage`, a draft can hold PHI)._

![Composer with text, Send enabled](images/frontend/02-composer-typing.png)

![Slash command menu](images/frontend/03-composer-slash-menu-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Command list | `/denials`, `/trend`, `/carc`, `/ar` insert a prompt; `/new`, `/search`, `/export`, `/light`, `/dark`, `/system`, `/settings`, `/shortcuts` run an action. |
| 2 | Active option | ↑ ↓ move, Tab or Enter runs, Esc dismisses (`aria-activedescendant`). |
| 3 | Textarea | Combobox; the menu shows only while the text is `/word`. |

![Attachment chips: pasted text and a .md file](images/frontend/04-composer-attachments-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Pasted chip | A paste of ≥ 1200 characters or > 15 lines becomes a chip instead of flooding the box. |
| 2 | File chip | Text file read locally; name and extension shown. |
| 4 | Send | Sends text + chips joined as one user message (`name:\ncontent`). |

![TODO screenshot: attachment rejected — attach a .png or a file over 256 KB; the red notice "scan.png isn't a text file" appears above the textarea](images/frontend/placeholder-attach-rejected.png)

![TODO screenshot: drag-over — drag a file onto the composer; it gets a focus-colour ring (data-dragging)](images/frontend/placeholder-attach-drag.png)

| State | Send button | Placeholder | Notes |
|---|---|---|---|
| Idle, empty | Disabled (30 % opacity) | "Ask about your claims" / "Ask a follow-up" | |
| Typing | Enabled | | Enter sends, Shift+Enter new line (or Ctrl/⌘+Enter when "Send with Enter" is off); IME composition never sends |
| Clarification open | Enabled | "Reply…" | The text resumes the run instead of starting a new turn |
| Streaming | Becomes **Stop** (Esc) | | |
| Offline | Disabled, tooltip "You're offline" | | Draft stays editable (§13) |

_TODO: document the boot shell (`lib/bootShell.ts`): text typed before JS loads is adopted as the new-chat draft._

---

## 8. Agent turn: thinking, streaming and steps

_TODO: intro: one collapsed row covers the whole think-and-query phase (ChatGPT/Claude style), the answer appears below it._

### 8.1 Sending / Thinking

![Thinking: question bubble, "Thinking" row, Stop button](images/frontend/05-sending-thinking-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Question | Added at once; the follow-up is pinned to the top of the viewport. |
| 2 | Thinking row | Shown from the moment of sending, before `message.start` (QA-A15). Same element later carries the steps, so nothing jumps. |
| 3 | Stop | Aborts the fetch and calls `POST /api/chat/{messageId}/stop`. |
| 4 | Sidebar entry | New thread appears right away. |

### 8.2 Live step line and expanded live steps

![Live: one muted line naming the current step](images/frontend/06-streaming-live-step.png)

![Live steps expanded: failed query, escalation, repair running, table skeleton](images/frontend/07-streaming-steps-expanded-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Live header | Current step's label ("Reconcile claim totals"), no spinner or ticking timer; fades when it changes (off under reduced motion). |
| 2 | Running step | Dot marks the step still running; it turns ✓ or ✕ in place (rows keyed by `stepId`). |
| 3 | Escalation note | "Escalated to Sonnet · repairs ran out" (`escalation` event). |
| 4 | Table skeleton | While a SQL/repair step runs and its table has not arrived. |
| 5 | Stop | Available throughout. |

### 8.3 Text streaming

![Answer prose streaming with caret, steps collapsed to "Thought for 3.1s"](images/frontend/08-streaming-text-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Caret | Paced text (`lib/pacedText.ts`) flows in over frames even when it arrives in one chunk; at once under reduced motion. |
| 2 | Timeline header | Collapses to "Thought for N s · Ran 3 queries, 1 repair, 2 checks" as soon as the answer starts after the last step. |

![Finished mock answer with repair and escalation in the steps](images/frontend/09-answer-v1-steps-repair-escalation.png)

### 8.4 Steps after the answer

![Steps expanded after the answer](images/frontend/13-steps-expanded-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Header | "Thought for 1.8s · Ran 2 queries, 1 check" (never shorter than its longest step, QA-A16). |
| 2 | First step | "Looked at sem.claims ✓ 4ms". |
| 3 | Last step | "Verified the result ⚠" (a check with a warning). |

![Each step's detail open: SQL, rows, timings](images/frontend/14-step-details-open.png)

### 8.5 Stopped by the user

![Stopped: steps closed as stopped, "Stopped" note, action row](images/frontend/52-stopped-by-user-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Header | "Thought for 3.1s · Ran 2 queries, 1 repair, 1 stopped". |
| 2 | Stopped note | `finishReason: aborted`; Retry only when nothing came back. |
| 3 | Action row | Copy, thumbs, Regenerate on what did arrive. |

_TODO: does the UI show a distinct "Stopping…" between `/stop` and `message.end` (CONTRACT.md §8 recommends it)? Not observed locally; the stop landed within one frame._

### 8.6 Follow-ups, edits and versions

![Editing a question inline: Cancel / Send](images/frontend/118-edit-question-inline.png)

![Edited question shows a version switcher](images/frontend/119-edited-version-nav-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Question versions | `‹ 2/2 ›` (`role="group"` "Versions", read as "2 of 2"). The edit is sent with `editOf` and stored as a sibling; the old tail is kept as version 1. |

![Regenerated answer with a version switcher](images/frontend/120-regenerated-answer-versions-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Answer versions | Regenerate sends `regenerate {messageId}`, `messages: []`; the reply is a sibling with `regenerateOf`. |

![More concise regeneration labelled "Concise"](images/frontend/121-regenerated-concise-style.png)

![Ask to change response input](images/frontend/122-ask-to-change-response.png)

![Jump to latest after scrolling up](images/frontend/123-jump-to-latest-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Jump to latest | Appears when the user scrolls away from the bottom; releases the pinned follow-up. |

![Long pasted question collapsed with Show more](images/frontend/124-long-question-collapsed.png)

![TODO screenshot: branch in new chat — More actions → Branch in new chat on an earlier answer; a new chat opens with the thread up to that answer and the toast "Branched into a new chat"](images/frontend/placeholder-branch.png)

![TODO screenshot: print one answer — More actions → Print this answer; capture the print preview (only that question and answer)](images/frontend/placeholder-print.png)

---

## 9. How the agent behaves and operates (user's view)

_TODO: one paragraph on the agent: one analyzer configuration (bundle B1: Haiku, low effort, single agent; contract 1.24.0), no model or strategy choice in the UI, model names are labels only. Link to backend page §7 (Analyzer)._

### 9.1 Request lifecycle

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant C as Composer / useChat
    participant A as HttpAnalystAdapter
    participant API as POST /api/chat (FastAPI)
    participant AN as Analyzer (KPI gate / Claim Query Agent)
    participant G as SQL guard + DuckDB
    U->>C: Send question
    C->>C: user bubble, "Thinking" row, Stop button
    C->>A: streamTurn({conversationId, messages, signal})
    A->>API: POST /api/chat (SSE, x-cc-csrf / demo bearer)
    API-->>A: message.start {messageId, model}
    API-->>A: status {label}
    loop iterative querying
        AN->>G: run_sql / describe / sample
        API-->>A: step.explore / step.sql_running / step.sql_result
        alt query fails
            API-->>A: step.repair_attempt → step.sql_running (repairOf)
        end
        opt small model gives up
            API-->>A: escalation {from, to, trigger}
        end
        API-->>A: step.check {verdict}
    end
    API-->>A: text.start / text.delta* / text.end
    API-->>A: part (code, table, chart, callout, file, artifact)
    API-->>A: message.end {finishReason, usage, sources?, conversationTitle?}
    A-->>C: reduceV1() each event
    C-->>U: answer, Sources, action row, usage
    Note over A,API: drop → GET /api/chat/{id}/stream with Last-Event-ID (250 ms, 1 s, 2 s, 4 s)
    U-)A: Stop → POST /api/chat/{id}/stop → message.end aborted ≤ 1 s
```

_TODO: confirm the KPI gate path (`via=template:<id>@v1` vs `b1`) shows up in step labels on the deployed build; LOCAL_STATE.md says the UI step labels were not checked in a browser._

### 9.2 Turn state machine

```mermaid
stateDiagram-v2
    [*] --> Idle
    Idle --> Sending: Send / Enter
    Sending --> Thinking: request accepted
    Sending --> Failed: HTTP 4xx/5xx, network error
    Sending --> RateLimited: 429 (Retry-After)
    RateLimited --> Sending: countdown ends (max 2 tries)
    Thinking --> Stepping: step.* / escalation
    Stepping --> Stepping: more steps
    Thinking --> Answering: text / table / chart part
    Stepping --> Answering: answer part after last step
    Answering --> Done: message.end stop
    Stepping --> NeedsInput: clarification + needs_input
    Thinking --> Abstained: abstain + abstain
    Stepping --> Abstained
    NeedsInput --> Sending: reply (resume)
    Thinking --> Reconnecting: stream drop
    Stepping --> Reconnecting
    Answering --> Reconnecting
    Reconnecting --> Stepping: replay from Last-Event-ID
    Reconnecting --> Failed: retries used / stream_gone
    Thinking --> Stopped: Stop
    Stepping --> Stopped
    Answering --> Stopped
    Failed --> Sending: Retry
    Stopped --> Sending: Retry / Regenerate
    Done --> Sending: follow-up, edit, regenerate
    Done --> [*]
```

### 9.3 Stream events → what the user sees

| Event (contract 1.24.0) | Payload highlights | UI effect | Screenshot |
|---|---|---|---|
| `message.start` | `messageId`, `conversationId`, `contractVersion`, `model?`, `regenerateStyle?`, `temporary?` | Swaps in the server id (row keeps its `uiKey`); Stop now also calls `/stop` | 05 |
| `status` | `label` or `null` | Transient line; hidden while the timeline is live unless it is a transport status | 86, 88 |
| `step.explore` | `probe`, `target`, `summary`, `elapsedMs` | "Looked at sem.claims", "Read schema", "Used the full EDI data" (`request_escalation`) | 13 |
| `step.sql_running` | `sql`, `purpose` (explore/answer/candidate/repair/check), `attempt`, `label`, `maskedLiterals?` | Running row "Running query" / server label; table skeleton | 07 |
| `step.sql_result` | `ok`, `elapsedMs`, `rowCount`, `columns`, `error {code}` | Row turns ✓ "7 rows · 8ms" or ✕ with the error; `cancelled` → "stopped" | 30, 47 |
| `step.repair_attempt` | `failedStepId`, `attempt/maxAttempts`, `error`, `revisedSql`, `explanation` | "Repairing the query" → "Repaired the query", error text in red | 30, 43 |
| `step.check` | `check`, `verdict` pass/warn/fail, `message`, `action` | "Verified the result ⚠", "Ran 2 checks" | 13, 75 |
| `step.candidates` | `candidates[]`, `chosenId`, `method`, `rationale` | "Compared 3 candidates · execution agreement" | 32 |
| `escalation` | `from`, `to` (model, tier), `reason`, `trigger` | "Handed off to Sonnet 5.5" step + note under the timeline | 31 |
| `reasoning.delta` / `reasoning.end` | summary text, `durationMs` | Folded into the timeline (one "Thought for" row) | 13 |
| `text.start` / `text.delta` / `text.end` | markdown | Paced prose with caret | 08 |
| `code.delta` | | Streaming code part | — |
| `table.start` / `table.rows` | columns, then rows with `offset` | Header first, rows fill in; dropped if the turn aborts | _TODO_ |
| `chart.start` / `chart.points` | strict `PlotHeader`, points | Chart draws from the first points | _TODO_ |
| `part` / `part.patch` | complete part / shallow patch | Part rendered or replaced by id | 10 |
| `message.end` | `finishReason` stop/abstain/needs_input/error/aborted, `usage`, `sources?`, `conversationTitle?` | Action row, Sources, usage line, title in sidebar | 12 |
| `heartbeat` | `at?` (no `seq`) | Nothing visible; resets the 45 s idle guard | — |

Unknown events and fields are ignored; unknown part types render as "Unsupported content" (S63).

### 9.4 Step types and labels

| Step kind | Icon | Live label → done label | Source |
|---|---|---|---|
| explore | Telescope | "Probing the data" → "Looked at <relation>" / "Read schema" / "Sampled values" / "Looked up glossary" / "Used the full EDI data" | `answer/normalize.ts` `PROBE` |
| sql | Database | "Running query" → "Ran query"; check purpose → "Ran check query"; final → "Final query" | `PURPOSE` / `PRESENT` |
| repair | Wrench | "Repairing the query" → "Repaired the query" ("Re-ran repaired query · attempt 2") | `normalize.ts` |
| check | ListChecks | "Verified the result" / "Checked row count" / "Ran N checks" | `CHECK` |
| candidates | GitCompare | "Compared N candidates · <method>" | |
| escalation | MoveUpRight | "Handed off to <model>" | |
| chart / schema / search / other | BarChart3 / List / Search / Wrench | legacy `tool_call` parts | |

### 9.5 Outcomes the user can get

| Outcome | finishReason | What it looks like | Screenshot |
|---|---|---|---|
| Answer | `stop` | Prose + table(s) + SQL + chart + callouts | 10, 36, 41 |
| Answer after repair | `stop` | Red failed steps, then repaired query | 30, 43 |
| Answer after escalation | `stop` | "Handed off to Sonnet 5.5", note "repairs ran out" | 31 |
| Answer from candidates | `stop` | "Compared 3 candidates" | 32, 75 |
| Empty result | `stop` | "Query returned 0 rows" + caveat | 40 |
| Clarification | `needs_input` | Question + choices; multi-question form; nested | 38, 39, 45 |
| Abstain | `abstain` | "The data needed for this isn't available" + Try asking | 33, 44 |
| Refusal (PHI) | `stop` _(TODO: confirm)_ | "I can't share patient-identifying details" | 34 |
| Error | `error` | "Something went wrong" + Retry | 35 |
| Stopped | `aborted` | "Stopped" + closed steps | 46–52 |

![Repair: binder error, repaired query, re-run](images/frontend/30-agent-repair.png)

![Escalation to a larger model](images/frontend/31-agent-escalation.png)

![Candidates compared](images/frontend/32-agent-candidates.png)

![Abstain with suggestions](images/frontend/33-agent-abstain.png)

![Refusal of patient identifiers](images/frontend/34-agent-phi-refusal.png)

![Analysis error with Retry](images/frontend/35-agent-error.png)

![Monthly trend: table and line chart](images/frontend/36-agent-chart-trend.png)

![Report artifact opens in the panel](images/frontend/37-agent-report-artifact.png)

![Clarification with two choices](images/frontend/38-agent-clarify.png)

![Multi-question clarification form](images/frontend/39-agent-clarify-multi.png)

![Empty result](images/frontend/40-agent-empty-result.png)

![Two tables in one answer](images/frontend/41-agent-multi-table.png)

![Long SQL, wide result table](images/frontend/42-agent-long-sql.png)

![Four repairs before the answer](images/frontend/43-agent-many-repairs.png)

![Escalation that still abstains](images/frontend/44-agent-escalation-abstain.png)

![Nested clarification (first question)](images/frontend/45-agent-clarify-nested.png)

### 9.6 Limits and escalations shown to the user

| Limit | Value / source | What the user sees |
|---|---|---|
| Repair attempts | `step.repair_attempt.maxAttempts` (demo: up to attempt 5) | Each attempt as its own step; "repairs ran out" on escalation |
| Escalation | `escalation.trigger`: verifier_fail, low_confidence, repair_exhausted, complexity, disagreement, user | "Handed off to …"; usage popover lists both models |
| Data escalation (lossless) | `request_escalation(reason)` → `step.explore` probe `request_escalation` | "Used the full EDI data" / "Asked for the full EDI data" when refused |
| Concurrent runs | 3 unfinished runs per user (`CC_API_MAX_CONCURRENT_RUNS`) → 429 | "Busy right now, retrying in Ns…" |
| Request body | 1 MiB (`CC_API_MAX_BODY_BYTES`) → 413 | "Message too long" |
| Preview rows | ≤ 50 rows in the table part; full CSV via `GET /api/download/{id}` (15 min, principal-bound) | "Showing 7 of 7 rows" / file card |
| Stream idle | 45 s client guard; server heartbeat every 15 s | S67 |
| Reconnect | 4 retries (250 ms, 1 s, 2 s, 4 s); replay kept 60 s after the run | S56, S57 |
| Disconnect grace | 10 s server-side, then the run is cancelled | S84 |
| Literal masking | `Capabilities.sqlLiteralMasking` (phi / all / off), `maskedLiterals` | `?` in shown SQL, "N values masked" (`sql-masked`) |

_TODO: add the analyzer's own budgets (`max_turns`, `max_tool_calls`, `row_limit` 5000, `time_limit_seconds`) once the deployed values are confirmed (backend page §7)._

### 9.7 Guard rejection

When the agent's SQL names something outside the allowed set (only `sem.*`; `lossless.*` only after an audited escalation), the guard blocks it before DuckDB runs. The user sees a failed step and, in the mock, an `access_denied` error part that offers the semantic views instead.

![Guard rejection: failed step "Queried raw.x12_837_loop2300", "Access denied" with Retry and Details](images/frontend/74-guard-rejection-access-denied-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Timeline header | "Thought for 1s · Ran 1 query · 1 failed". |
| 2 | Failed step | "Blocked by guard: only sem.* views are allowed." |
| 3 | Error part | `code: access_denied`, "Raw EDI tables are outside your scoped session…"; Details shows `guard=run_semantic_sql; relation=…; policy=sem_only`. |
| 4 | Retry | Re-runs the same question (regenerate). |

> **Note:** This capture is the **mock** fixture. On the real analyzer a guard error usually goes back to the model as a `step.sql_result {ok:false, error.code: "guard_blocked"}` and is repaired rather than surfaced. _TODO: capture a real `guard_blocked` step from the deployed app._

### 9.8 Cancellation at each stage

![Stop while planning](images/frontend/46-cancel-plan.png)

![Stop while a query runs](images/frontend/47-cancel-sql.png)

![Stop during repair](images/frontend/48-cancel-repair.png)

![Stop during candidates](images/frontend/49-cancel-candidates.png)

![Stop after escalation](images/frontend/50-cancel-escalation.png)

![Stop while rendering](images/frontend/51-cancel-render.png)

_TODO: one line per stage on what is kept: steps up to the stop, no partial table, `usage.queries` counts the interrupted statement._

---

## 10. Message rendering parts

_TODO: intro: the answer is a list of typed parts (`Part` union in `contract/v1.gen.ts`); each has a renderer in `components/parts/` or `components/answer/`._

| Part | Renderer | Notes |
|---|---|---|
| `text` | `Markdown` (Streamdown, GFM) | No raw HTML; remote images replaced by alt text; links `noopener noreferrer nofollow` |
| `reasoning` | folded into `StepTimeline` | Real backends send label/count summaries only |
| `tool_call` (legacy) | `StepTimeline` rows | Input SQL/args only, never result rows |
| `table` | `DataTable` | PHI columns masked, sortable, virtualized > 200 rows |
| `chart` | `ChartView` (Vega-Lite, SVG) | Strict `PlotSpec` (1.18.0); `data.url` stripped |
| `code` | `CodePartView` / `CodeBlock` (Shiki) | `role: query` collapses under "View SQL" when linked to a table |
| `image` | `ImageView` | Fetched by opaque `blobId` into a revoked object URL |
| `file` | `FileCard` | Download via adapter blob / `GET /api/download/{id}` |
| `callout` | `Callout` | `assumption` (editable → "Change"), `caveat`, `data_quality`, `phi_redaction`, `info` |
| `artifact` | `ArtifactCard` → panel | kinds: code, report, … (open enum, fallback `report`) |
| `error` | `ErrorView` | Plain line, small red icon, Retry, Details |
| `abstain` | `AbstainCard` | category (open, fallback `unsupported`), suggestions |
| `clarification` | `ClarifyCard` / `ClarifyForm` | single or 1–4 questions |
| `status` | — | not rendered as a part |
| unknown | `Unsupported` | "Unsupported content (<type>)" |

### 10.1 Answer chrome: action row, sources, usage

![Action row: Assumptions and Check before using callouts, Sources chip, Copy, thumbs, Regenerate, style chevron, More, usage](images/frontend/12-answer-actions-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Assumption callout | "Change" pre-fills the composer with "Instead, assume ". |
| 2 | Sources chip | "Looked at sem.claims"; only when something was consulted (QA-A31). |
| 3 | Copy | Markdown of the answer; PHI columns dropped. |
| 4 | Good response | `PUT …/feedback {rating: up}` on server-known answers. |
| 5 | Bad response | Opens "Tell us more" (S18). |
| 6 | Regenerate | Re-runs as the next version (last answer only). |
| 7 | Answer style | Try again / More concise / More detailed / Ask to change response… |
| 8 | More actions | Branch in new chat, Print, Copy as Markdown, Copy SQL. |
| 9 | Usage / SQL | "SQL · 2 queries · 2s · <$0.01"; opens SQL inspector and the details popover. |
| 10 | Follow-ups | Suggested follow-up questions when the answer has them (not in this capture). |

![Table with its SQL disclosure open](images/frontend/15-table-sql-open.png)

![Answer details popover: tokens, cost estimate, queries, first token, total time, stage bar](images/frontend/16-answer-details-popover.png)

![SQL inspector](images/frontend/17-sql-inspector.png)

![Sources popover: "What the analyst used for this answer"](images/frontend/18-sources-popover.png)

![More actions menu](images/frontend/19-more-actions-menu.png)

![Regenerate menu](images/frontend/20-regenerate-menu.png)

![Feedback panel with reason chips](images/frontend/21-feedback-panel.png)

### 10.2 Prose, code and math

![Markdown: blockquote, GFM table, nested list with link, Python code block](images/frontend/141-markdown-code-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Blockquote | Styled quote. |
| 2 | GFM table | Markdown table (not a `table` part: no toolbar). |
| 3 | Link | Quiet underline; `rel="noopener noreferrer nofollow"`. |
| 4 | Code block | Shiki highlighting, Copy, Download, Open in panel (on hover), Wrap lines. |
| 5 | Language label | `code-lang`. |

![TODO screenshot: KaTeX math — send a turn whose text contains `$r = \frac{d}{n}$` and `$$…$$`; locally this crashed the whole app instead (see §21, open question 1)](images/frontend/placeholder-math.png)

### 10.3 Files, PHI masking, artifacts

![File card and View SQL under the table](images/frontend/59-file-card-and-sql-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | File card | `cc_denial_rate_by_payer_2026-10-25.csv · 6 rows · 309 B`; download; preview opens the matching table in the panel. |
| 2 | View SQL | Collapsed query linked to the table. |

![Markdown table and PHI-flagged sample](images/frontend/71-markdown-table-phi.png)

![PHI masked column, callouts and artifact card](images/frontend/72-phi-masked-table-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | PHI column | "Member ID" shown as `•••• 56`, shield icon, not sortable, excluded from copy and CSV. |
| 2 | PHI masked callout | "1 column (Member ID) was masked under your access level and is excluded from copy and CSV." |
| 3 | Data quality callout | Verifier gap note. |
| 4 | Artifact card | "CARC breakdown query · Code · Click to open". |

![Image part with caption and a Caveat callout](images/frontend/70-image-part-callout-caveat.png)

![TODO screenshot: image lightbox — mock mode, ask "Plot the monthly denial rate trend for our top 3 payers", hover the figure, press Expand image](images/frontend/placeholder-lightbox.png)

### 10.4 Clarify and abstain cards

![Clarify card](images/frontend/76-clarify-card-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Clarifying question | `role="group"` "Clarifying question"; only the last turn is active. |
| 2 | Choice chip | Sends `resume {clarificationId, choiceIds}`. |
| 3 | Composer | Placeholder "Reply…"; free text resumes too (`allowFreeText`). |

![Clarification answered, run resumed](images/frontend/77-clarify-answered-resumed.png)

![Abstain card](images/frontend/78-abstain-card-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Reason | Markdown reason, `data-category` (data_unavailable, unsupported, …). |
| 2 | What would be needed | `details`. |
| 3 | Try asking | Suggestion chips send a new question. |

![TODO screenshot: multi-question clarification answered — demo API, ask "Ask me a few questions about the denial rate breakdown", answer Metric and Period, Skip Group by, Submit](images/frontend/placeholder-clarify-multi-answered.png)

### 10.5 Candidates and checks (mock)

![Probe, failed query, repair with error box, candidates and checks expanded](images/frontend/75-candidates-and-checks.png)

Full-page version: `images/frontend/75-candidates-and-checks-full.png`.

---

## 11. Artifact panel

_TODO: intro: the right-hand panel for tables, charts, code, images and reports; read-only result views, versioned._

![Artifact panel with a table: tabs, actions, body, resize handle](images/frontend/63-artifact-panel-table-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Resize handle | `role="separator"`; drag or ←/→; double-click resets to 50 %; min 360 px, max 75 %; width stored as `cc.pref.artifactWidth`. |
| 2 | Tabs | Table | SQL · Chart | Spec | Data · Code · Preview/Markdown (report). |
| 3 | Copy | Copies the current tab. |
| 4 | Download | CSV / PNG / SVG / file. |
| 5 | Full screen | Hidden on phones. |
| 6 | Close | Esc. |
| 7 | Body | Table with "Filter rows" (panel only). |

![SQL tab](images/frontend/64-artifact-panel-sql-tab.png)

![Panel resized with the keyboard](images/frontend/65-artifact-panel-resized.png)

![Chart in the panel](images/frontend/68-artifact-panel-chart.png)

![Chart Spec tab](images/frontend/69-artifact-panel-chart-spec.png)

![Chart Data tab](images/frontend/69-artifact-panel-chart-data.png)

![Code artifact in the panel](images/frontend/73-artifact-panel-code.png)

_TODO: versions (`artifact-versions`, "v1/v2"), auto-open rules (`autoOpenIds`, `userIsTyping`), and print report._

---

## 12. Tables and charts

### 12.1 Tables

![Table toolbar on hover](images/frontend/60-table-toolbar-hover-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Title | Real titles only; a generic "Result" is hidden. |
| 2 | Copy table | TSV, PHI columns dropped. |
| 3 | Download CSV | Server `csvFileId` first (`GET /api/download/{id}`), client CSV of the preview as fallback; CSV injection guarded. |
| 4 | Expand | Opens the panel. |
| 5 | Column header | Click to sort; numeric columns right-aligned. |
| 6 | Callout above | Assumptions for this answer. |

![Sorted by Denial rate](images/frontend/61-table-sorted.png)

![Column resized](images/frontend/62-table-column-resized.png)

![600-row, 12-column table: horizontal scroll with edge fade, virtualized rows](images/frontend/142-wide-tall-table-overflow-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Right fade | `table-fade-right`: more columns off to the right; the table scrolls in its own frame, never the page. |
| 3 | Table | > 200 rows virtualized (TanStack Virtual) with spacer rows; "Open full table". |

### 12.2 Charts

![Chart hover toolbar](images/frontend/66-chart-hover-toolbar-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Download | PNG / SVG / CSV of the data. |
| 3 | Expand chart | Opens the panel (Chart / Spec / Data). |
| 4 | Plot | `role="img"` with the part's `alt`; theme-aware; tooltips escaped through the Trusted Types policy. |

![Picked point: "Ask about Northwind Health Plan"](images/frontend/67-chart-point-ask.png)

_TODO: progressive charts (`chart.start` / `chart.points`, contract 1.18.0) and the client plot gate (`contract/plot.ts`)._

---

## 13. Errors and recovery

_TODO: intro: errors are a plain line in the answer column (no tinted box) with one Retry; transport states are a status line while the turn is still alive; nothing spins forever._

### 13.1 HTTP errors before the stream starts

`httpError()` in `adapters/http.ts` resolves `error.code` → `error.legacyCode` → HTTP status.

| Status | code | Title | Retry | Screenshot |
|---|---|---|---|---|
| 400 / 422 | `validation` | Couldn't send that | no | 82 |
| 401 | `auth_expired` | Session expired (cookie session: redirect to sign-in or `/signed-out?reason=expired`) | no | 91, 93 |
| 403 | `access_denied` | Not available | no | — |
| 404 | `not_found` | Not found | no | — |
| 404 | `stream_gone` | Answer no longer streaming | no | — |
| 405 | `method_not_allowed` | Not supported | no | — |
| 409 | `clarification_expired` / `conflict` | Ask again | no | 83 |
| 410 | `transcripts_unavailable` | Chat unavailable | no | P-410 |
| 413 | `payload_too_large` | Message too long (limit from `maxBytes`) | no | 81 |
| 422 | `invalid_title` | Couldn't rename | no | — |
| 429 | `rate_limited` | Too many requests (auto-retry first, §13.2) | yes | 86 |
| 5xx | `internal` | Analyst unavailable (generic copy, never the server message) | yes | 80 |
| other 4xx | raw code | Request not accepted | no | — |
| no response | `internal` / `offline` | Can't reach the analyst / You're offline | yes | 84 |

![HTTP 500](images/frontend/80-error-http-500.png)

![HTTP 413](images/frontend/81-error-payload-too-large.png)

![HTTP 422](images/frontend/82-error-validation-422.png)

![HTTP 409 clarification expired](images/frontend/83-error-clarification-expired.png)

![Network failure](images/frontend/84-error-network-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Error line | "Can't reach the analyst · The server did not respond. Try again in a moment." |
| 2 | Retry | Re-sends; the composer kept the question. |
| 3 | Action row | Copy only on a failed turn (QA-A22). |

![After Retry](images/frontend/85-error-retry-recovered.png)

![Session expired in chat (demo token)](images/frontend/91-auth-expired-in-chat.png)

![TODO screenshot: 410 transcripts_unavailable — route GET /api/conversations/{id} to 410 {error:{code:"transcripts_unavailable"}} and open the chat](images/frontend/placeholder-410.png)

### 13.2 Transport: rate limit, reconnecting, offline

![429: "Busy right now, retrying in 8s…"](images/frontend/86-rate-limited-countdown-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Status line | Countdown from `Retry-After` (seconds or HTTP date against the response `Date`), up to 2 automatic retries (`maxRateRetries`), 30 s max wait. |
| 2 | Stop | Cancels the wait. |

Dev shortcut: `?fault=429` on a `vite dev` build makes the first try of each answer 429 (`demoFaultOption`).

![Reconnecting… after a mid-answer drop](images/frontend/88-reconnecting.png)

![Connection lost after the replay was gone](images/frontend/89-connection-lost.png)

![Offline pill, Send disabled, draft kept](images/frontend/90-offline-banner-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Offline pill | After 1 s of continuous offline (`useSettledOnline`); hides as soon as the connection is back. |
| 2 | Send | Follows raw `navigator.onLine`, so nothing is sent into a blip. |
| 3 | Draft | Stays editable. |

![TODO screenshot: stalled stream — route POST /api/chat to send only message.start and then nothing for 45 s; before message.start the turn ends with "The analyst stopped responding" (retryable)](images/frontend/placeholder-stalled.png)

![TODO screenshot: answer too large — serve one SSE data line over 32 MiB without a newline; the answer keeps what arrived and adds "Answer too large to show"](images/frontend/placeholder-too-large.png)

### 13.3 Render failures

![Chart chunk failed to load](images/frontend/96-chart-chunk-load-failed.png)

![App-level crash screen (real crash: inline math, see §21)](images/frontend/97-app-crash-boundary-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Heading | "Something went wrong" (or "Couldn't load part of the app" for a chunk error). Never shows the error message (PHI). |
| 2 | Actions | Reload (focused) · Start a new chat (drops the `#/c/…` route so a crashing chat can't trap the user). |
| 3 | Diagnostics | Reference id + allowlisted facts (build, contract, browser, theme, online), "Copy diagnostics". |

![TODO screenshot: section crash — make one part throw while rendering (e.g. a malformed artifact in a test build); the part is replaced by "This part couldn't be displayed. Ref …" with Try again / Close](images/frontend/placeholder-section-crash.png)

![Unknown part type](images/frontend/95-unsupported-part.png)

### 13.4 Status pages and sign-in

![Signed out](images/frontend/92-signed-out.png)

![Session expired](images/frontend/93-session-expired.png)

![Not found](images/frontend/94-not-found.png)

![TODO screenshot: cookie sign-in — build with VITE_AUTH=cookie VITE_SIGN_IN_URL=/api/auth/mock-login against api.sim_server; capture the sign-in redirect and the first load after sign-in (ana@xifin.example)](images/frontend/placeholder-sign-in.png)

![TODO screenshot: signed out in another tab — sign out in tab A; tab B leaves to the signed-out page (BroadcastChannel)](images/frontend/placeholder-cross-tab.png)

---

## 14. Responsive & touch

![Phone, empty chat](images/frontend/130-mobile-empty-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Open sidebar | Opens the drawer (also left-edge swipe, `useEdgeSwipeToOpen`). |
| 2 | Suggestion cards | One swipeable row of two-line cards above the composer; doesn't raise the keyboard (`pointer: coarse`). |
| 3 | Composer | Full width; `enterKeyHint="send"`. |
| 4 | More options | Same menu as desktop; New chat icon sits next to it. |

![Phone drawer open](images/frontend/131-mobile-drawer-open-annotated.png)

| # | Element | Behaviour |
|---|---|---|
| 1 | Drawer | `role="dialog"` `aria-modal`; choosing a chat or New chat closes it; swipe to close. |
| 2 | Scrim | Tap to close. |

![Phone answer](images/frontend/132-mobile-answer.png)

![Artifact panel as a full-screen sheet on the phone](images/frontend/133-mobile-artifact-sheet.png)

![Action row always visible on touch (no hover)](images/frontend/134-mobile-actions-always-visible.png)

![Tablet, 820 px](images/frontend/135-tablet-820.png)

![TODO screenshot: edge swipe — on a 390 px touch viewport, swipe from the left edge to open the drawer and swipe left to close (e2e/shell-swipe.spec.ts)](images/frontend/placeholder-swipe.png)

_TODO: 200 % zoom (640 px CSS viewport): no page-level horizontal scroll (`e2e/answer-edge.spec.ts`)._

---

## 15. Themes and palettes

![Neutral, dark](images/frontend/136-theme-neutral-dark.png)

![Paper, light](images/frontend/137-theme-paper-light.png)

![Paper, dark](images/frontend/138-theme-paper-dark.png)

_TODO: theme follows the OS on "System"; changes sync across tabs (`onPrefChange`); single-series charts follow the palette accent._

---

## 16. Accessibility

| Area | Behaviour | Code / test |
|---|---|---|
| Live regions | One shell status region ("answering…" / "Answer complete"); transport announcer is polite and throttled (429 countdown spoken at start, every 5 s, and on retry) | `TransportAnnouncer`, `lib/transportAnnounce.test.ts` |
| Step timeline | Group named by its header; new steps spoken once; never the timer | `StepTimeline`, `answer/thinkingDisclosure.test.tsx` |
| Focus management | Edit/version switches put focus back on the matching control (QA-A21); dialogs trap focus; Esc closes | `shell/focus.test.tsx`, `answer/qaA21A22.test.tsx` |
| Keyboard | Roving focus in the sidebar list; slash menu via `aria-activedescendant`; resizable panel and columns by arrow keys | `e2e/keyboard-sr.spec.ts` |
| Reduced motion | Fades and paced text off | `lib/motion.ts`, `e2e/answer-edge.spec.ts` |
| Automated checks | axe-core in Playwright | `e2e/a11y.spec.ts` |

![Screen-reader-only transport announcer made visible for the capture](images/frontend/87-a11y-transport-announcer-revealed.png)

![Keyboard focus ring](images/frontend/125-keyboard-focus-ring.png)

_TODO: list the ARIA names QA should use (Message, Send message, Stop generating, Chat history, Artifact: <title>, Resize panel, …)._

---

## 17. Privacy and PHI rules visible in the UI

| Rule | How the UI shows or enforces it |
|---|---|
| No PHI in URLs | Chats are `#/c/<opaque id>`; search is POST; downloads use opaque ids and revoked `blob:` URLs |
| No chat data in browser storage | Only `cc.pref.*` keys (`theme`, `palette`, `sidebarCollapsed`, `threadWidth`, `artifactWidth`, `enterToSend`); drafts and attachments in memory only |
| No chat text in the tab title | Fixed titles ("CC", "Page not found · CC", …) |
| Masked columns | `phi: true` → `•••• 56`, not sortable, dropped from copy/CSV/Markdown export |
| Generated titles | Derived server-side from column identifiers only; the question is never echoed |
| Error copy | 5xx never shows server text; crash screen never shows the error message |
| Feedback comment | Placeholder "(optional, no patient identifiers)"; sent only to the feedback endpoint |
| Sharing | Removed (contract 1.22.0) |

_TODO: link to `docs/frontend_security.md` and the backend page §9._

---

## 18. Frontend ↔ API contract

Contract version **1.24.0** (`web/src/contract/v1.gen.ts`, generated from the API schema; `api/CONTRACT.md` is the prose).

### 18.1 Endpoints the web app calls

| Method & path | Used for | Client code |
|---|---|---|
| `POST /api/chat` | Ask / follow-up / edit (`editOf`) / regenerate (`regenerate {messageId, style}`) / clarify reply (`resume`) → SSE | `adapters/http.ts` |
| `GET /api/chat/{messageId}/stream` (+ `Last-Event-ID`) | Re-attach after a drop | `adapters/http.ts` |
| `POST /api/chat/{messageId}/stop` | Stop | `adapters/http.ts` |
| `GET /api/download/{id}` | Full CSV / file (15 min, principal-bound, audited) | `adapters/http.ts` |
| `GET /api/capabilities` | Feature flags (once per session) | `contract/capabilities.ts` |
| `GET /api/transcripts` · `PUT /api/transcripts/retention` | History mode / retention | `adapters/httpStore.ts` |
| `GET /api/conversations?cursor=&archived=` | Sidebar (`exclude`), Settings → Archived (`only`) | `adapters/conversationsApi.ts` |
| `POST /api/conversations/search` | Search chats | `conversationsApi.ts` |
| `GET /api/conversations/{id}` | Open a chat (steps replayed by `reduceV1`) | `httpStore.ts` |
| `PATCH /api/conversations/{id}` | Rename / pin / archive | `httpStore.ts` |
| `DELETE /api/conversations/{id}` | Delete (and discard a temporary chat) | `httpStore.ts` |
| `GET /api/conversations/{id}/export?format=json\|markdown` | Export as JSON (live API) | `conversationsApi.ts` |
| `PUT` / `DELETE /api/conversations/{cid}/messages/{mid}/feedback` | Thumbs + reasons + comment | `lib/feedback.ts` |
| `POST /api/auth/sign-out` | Sign out (204, `Clear-Site-Data`) | `auth/session.ts` |

Every cookie-authenticated write sends `x-cc-csrf: 1`. Demo builds send `Authorization: Bearer demo:<customer>:<user>`.

### 18.2 Request shape

```json
{
  "conversationId": "c_…",
  "messages": [{ "id": "u_…", "role": "user", "text": "…", "parentId": "…", "editOf": "…" }],
  "resume": { "clarificationId": "…", "choiceIds": ["c2"], "text": "…", "answers": [{ "questionId": "metric", "choiceIds": ["dollar_rate"] }] },
  "regenerate": { "messageId": "m_…", "style": "default | concise | detailed" },
  "temporary": true
}
```

`options.strategy`, `options.model` and any identity-scoped key (`customer`, `tenant`, `schema`, `tables`, …) are 422: the data scope comes from the signed-in identity only (CONTRACT.md §2.1).

### 18.3 Stream framing

`id: <messageId>:<seq>` + `data: <one StreamEvent JSON>`; heartbeats every 15 s with no id. Events and payloads: §9.3. Usage (`message.end.usage`): `queries`, `elapsedMs`, `tokens`, `costUsd` (+ `costIsEstimate`), `latency {firstStepMs, firstTokenMs, sqlMs, totalMs, stages {plan, sql, repair, checks, render}}`, `models[]`.

_TODO: link the canonical example streams in `api/contract/examples/` (answer, tool_steps, repair, abstain, clarify, escalation, chart, candidates, error, report, edge/*)._

---

## 19. Configuration and feature flags

| Setting | Where | Effect |
|---|---|---|
| `VITE_ANALYST_BASE` | build env | unset/`''` → offline mock adapter + memory store; `/` → same origin (Vite proxies `/api` to `CC_API_URL`, default `http://127.0.0.1:8787`); URL → that base |
| `VITE_ANALYST_DEMO_TOKEN` | build env | Demo bearer `demo:<customer>:<user>`; build refuses anything else |
| `VITE_AUTH=cookie` + `VITE_SIGN_IN_URL` | build env | Real backend: `cc_session` cookie + CSRF header; 401 → sign-in URL `?next=` or `/signed-out?reason=expired` |
| `VITE_TRANSCRIPTS=memory` | build env | Force the in-memory store even against the live API |
| `VITE_DEMO_FAULTS=1` | build env | Keep `?fault=429` in a non-dev build |
| `CC_API_URL`, `CC_PREVIEW` | Vite server env | Proxy target; preview nonce handling |
| `?fault=429` | URL (dev) | First try of each answer gets 429 |
| `?audit` | URL | Render profiling marks |
| `Capabilities.feedbackReasons` | server | Reason chips |
| `Capabilities.sqlLiteralMasking` | server | `phi` / `all` / `off` masking of shown SQL |
| `Capabilities.pinArchive` | server | Pin/Archive actions and archived filter |
| `Capabilities.temporaryChat {ttlSeconds}` | server | Temporary chat on the server side |
| `Capabilities.demo` | server | Demo scenarios honoured |
| `cc.pref.*` | localStorage | theme, palette, sidebarCollapsed, threadWidth, artifactWidth, enterToSend |
| Server: `CC_API_MAX_CONCURRENT_RUNS`, `CC_API_MAX_BODY_BYTES`, `CC_API_HEARTBEAT_SECONDS`, `CC_API_DISCONNECT_GRACE_SECONDS`, `CC_API_TEMPORARY_TTL_SECONDS` | API env | Limits in §9.6 |

_TODO: record the deployed values._

---

## 20. Testing coverage map

| Area | Unit (Vitest, `web/src/`) | E2E (Playwright, `web/e2e/`) |
|---|---|---|
| Shell, routes, focus | `shell/shell.test.tsx`, `routes.test.tsx`, `focus.test.tsx`, `fidelity.test.ts`, `parityPass*.test.ts(x)`, `turnSpacing.test.tsx`, `toast.test.tsx` | `shell-routes`, `shell-focus`, `shell-fidelity`, `shell-titles`, `fidelity`, `parity-gaps` |
| Sidebar / history | `components/sidebarFilter`, `sidebarRail`, `sidebarTouch`, `shell/archive`, `SearchDialog`, `title`, `adapters/httpStore*`, `conversationsApi` | `shell-sidebar-filter`, `shell-find`, `shell-swipe` |
| Composer | `components/Composer.test.tsx`, `lib/bootShell.test.ts`, `lib/inputModality.test.ts` | `features`, `keyboard-sr` |
| Streaming & steps | `answer/thinkingRow`, `thinkingDisclosure`, `quietStream`, `liveTurnPacing`, `parts/streaming`, `render/streamingPrefix`, `lib/pacedText`, `lib/frameBatch`, `lib/tableReveal` | `answer-edge`, `visual-states` |
| Answer parts | `answer/answer`, `malformedParts`, `edgeScenarios`, `comparisonPass`, `dataWithheld`, `sources*`, `sqlMasking`, `followUps`, `messageActions`, `actionRowFidelity`, `noSlop`, `parts/*`, `render/*` | `contract-outcomes`, `mock`, `live`, `backend-fit` |
| Clarify / abstain | `contract/clarify`, `answer/clarifyForm`, `shell/clarifyReply` | `shell-clarify-reply` |
| Artifact panel | `components/ArtifactPanel`, `artifacts`, `parts/artifactKind` | `features` |
| Errors / transport | `adapters/http.*` (errors, httpError, ratelimit, midstream, streamgone, flapping, guard, backendFit), `lib/useChat.*`, `lib/fetchGuard`, `lib/serverClock`, `components/ErrorBoundary`, `sectionBoundaries`, `transportAnnouncer` | `chaos`, `edge`, `soak` |
| Auth | `auth/session`, `crossTabSignOut`, `bfcache` | `shell-routes` |
| Security / PHI | `security/*` (phi, titleSink, csvInjection, headers, trustedTypes, sri, noServiceWorker) | `share-removed` |
| Contract | `contract/*`, `api/contract*.test.ts`, `v1.test.tsx` | `contract-outcomes` |
| Visual / perf / print | `printCss.test.ts`, `perf/*` | `visual`, `visual-states`, `print`, `perf`, `memory200`, `long-session`, `a11y` |

_TODO: mark which suites run in CI (`docs/frontend_ci.md`) and which are opt-in (`e2e:chaos`, `e2e:xb`, `e2e:perf`)._

---

## 21. Open questions

1. **Inline and display math crash the whole app (observed locally).** A text part containing `$…$` or `$$…$$` threw `Failed to set the 'innerHTML' property on 'Element': This document requires 'TrustedHTML' assignment and the 'default' policy failed to execute` and the app-level ErrorBoundary replaced the page (`97-app-crash-boundary.png`). KaTeX output goes through `innerHTML`, but only Shiki and chart tooltips may mint TrustedHTML (`security/trustedTypes.ts`). Seen on the Vite dev server in Chromium; the same CSP is set for preview/production (`security/headers.ts`). The math unit tests run in jsdom, which has no Trusted Types. _TODO: confirm on a production build and on the deployed app; decide whether math should get its own policy or fail inside a section boundary._
2. **Chart load failure shows a raw URL.** When the Vega chunk fails, the chart frame shows "Chart failed to render: Failed to fetch dynamically imported module: http://127.0.0.1:5173/node_modules/.vite/deps/vega-embed.js?v=…" (`96`). Other surfaces deliberately hide raw error text. _TODO: confirm whether this is acceptable._
3. **Temporary chat on the live API.** The toggle only appears with the memory store, although the server announces `Capabilities.temporaryChat`. _TODO: intended?_
4. **PHI badge** says "De-identified" as static text. _TODO: which server signal should drive it?_
5. **Guard rejection on the real analyzer.** The access-denied card is a mock fixture; the real loop repairs `guard_blocked` silently. _TODO: what should a user see when the guard blocks every attempt?_
6. **KPI gate labels.** `api/live.py route_step via=template:<id>@v1|b1` — the UI step labels were never checked in a browser (LOCAL_STATE.md). _TODO._
7. **Refusal finishReason.** The PHI refusal renders as an abstain-style card; confirm whether the server sends `abstain` or `stop`.
8. **"Stopping…" state** between `/stop` and `message.end` (CONTRACT.md §8). Not observed. _TODO._
9. _TODO: screenshots from the deployed app (real sign-in, real data scope, real model labels)._

---

## 22. Glossary

| Term | Meaning |
|---|---|
| Turn | One question and the assistant message that answers it |
| Part | A typed piece of an answer (`text`, `table`, `chart`, `code`, `callout`, …) |
| Step | A `step.*` / `escalation` event shown in the timeline (not a part) |
| Step timeline | The collapsible "Thinking / Thought for N s" row |
| Artifact panel | The right-hand result viewer |
| Clarification | The agent asks before running (`needs_input`); answered via `resume` |
| Abstain | The agent declines because it can't answer reliably (`abstain`) |
| Escalation | The run moves to a larger model (`escalation` event); separately, *data* escalation mounts PHI-stripped `lossless.*` after an audited reason |
| Repair | A failed query is revised and re-run (`step.repair_attempt`) |
| Candidates | Several queries compared; one chosen (`step.candidates`) |
| Guard | Server-side SQL check: one read-only SELECT, `sem.*` only (`lossless.*` after escalation) |
| `sem.*` | Semantic layer views (gold layer); see backend page §4 |
| Transport status | Client-side status: offline, reconnecting, 429 countdown |
| Mock adapter | `MockAnalystAdapter`, offline fixture replay |
| Demo API | `api/` in demo mode: scripted LLM, synthetic DuckDB |
| PHI | Protected health information |
| CARC | Claim Adjustment Reason Code |

---

## 23. Screenshot index

Real captures (local, synthetic data) — all under `images/frontend/`:

`01-empty-first-run` (+annotated), `02-composer-typing`, `03-composer-slash-menu` (+annotated), `04-composer-attachments` (+annotated), `05-sending-thinking` (+annotated), `06-streaming-live-step`, `07-streaming-steps-expanded` (+annotated), `08-streaming-text` (+annotated), `09-answer-v1-steps-repair-escalation`, `10-answer-complete` (+annotated), `11-answer-complete-full`, `12-answer-actions` (+annotated), `13-steps-expanded` (+annotated), `14-step-details-open`, `15-table-sql-open`, `16-answer-details-popover`, `17-sql-inspector`, `18-sources-popover`, `19-more-actions-menu`, `20-regenerate-menu`, `21-feedback-panel`, `30`–`51` agent scenarios, `52-stopped-by-user` (+annotated), `59-file-card-and-sql` (+annotated), `60-table-toolbar-hover` (+annotated), `61-table-sorted`, `62-table-column-resized`, `63-artifact-panel-table` (+annotated), `64-artifact-panel-sql-tab`, `65-artifact-panel-resized`, `66-chart-hover-toolbar` (+annotated), `67-chart-point-ask`, `68-artifact-panel-chart`, `69-artifact-panel-chart-spec`, `69-artifact-panel-chart-data`, `70-image-part-callout-caveat`, `71-markdown-table-phi`, `72-phi-masked-table` (+annotated), `73-artifact-panel-code`, `74-guard-rejection-access-denied` (+annotated), `75-candidates-and-checks` (+full), `76-clarify-card` (+annotated), `77-clarify-answered-resumed`, `78-abstain-card` (+annotated), `80`–`83` HTTP errors, `84-error-network` (+annotated), `85-error-retry-recovered`, `86-rate-limited-countdown` (+annotated), `87-a11y-transport-announcer-revealed`, `88-reconnecting`, `89-connection-lost`, `90-offline-banner` (+annotated), `91-auth-expired-in-chat`, `92-signed-out`, `93-session-expired`, `94-not-found`, `95-unsupported-part`, `96-chart-chunk-load-failed`, `97-app-crash-boundary` (+annotated), `100`–`125` sidebar, dialogs, edit/versions, `130`–`142` responsive, themes, temporary chat, markdown, large table.

Placeholders (not rendered locally): `placeholder-attach-rejected`, `placeholder-attach-drag`, `placeholder-math`, `placeholder-lightbox`, `placeholder-clarify-multi-answered`, `placeholder-410`, `placeholder-stalled`, `placeholder-too-large`, `placeholder-section-crash`, `placeholder-sign-in`, `placeholder-cross-tab`, `placeholder-swipe`, `placeholder-branch`, `placeholder-print`.

_TODO: replace all with captures from the deployed app._

---

## 24. Change history

| Date | Author | Change |
|---|---|---|
| 2026-10-27 | _TODO_ | Skeleton created: state inventory, agent behaviour, contract map, 150+ local screenshots (synthetic data) |
| _TODO_ | _TODO_ | _TODO_ |
