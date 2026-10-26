# ClaimClarity frontend: handoff

**Owner: platform.** Other teams add their own sections in the marked slots below (contract: §5 analyzer adapter;
shell, answer: §9; QA: §11). This file is meant to be enough for someone with **no other context** (for example
Claude, picking up the repo) to run, test, ship and connect the real analyzer. Where a topic has a longer doc, this
file says what you need and links the rest.

State at handoff: branch `grokbot/frontend-sota`, contract version in `api/contract/__init__.py: CONTRACT_VERSION`
(1.21.0 when this was written; the sharing removal bumps it again, see §8). Everything runs offline in **demo mode**:
`ScriptedLLM`, synthetic DuckDB data, no paid model API, no network.

## 0. Ground rules that still apply

- **No PHI** in logs, console, `localStorage`, URLs, page titles or screenshots. Tests enforce this
  (`web/src/security/phi.test.tsx`, `titleSink.test.tsx`, `api/tests/test_adapter_r2_r4_r7.py`). Synthetic data only.
- **No paid API calls** unless the paid-API switch is deliberately turned on (§5, §7).
- **Data scope comes from the identity, never the request** (CONTRACT §2.1, BACKEND-FIT). The user never picks a
  customer, database, lake, schema or view; there is no UI for it and the API refuses such request keys (§3.3).
- The frontend consumes **only contract events** (`api/contract/`). Changing an event shape means a contract PR that
  bumps the version and regenerates `web/src/contract/v1.gen.ts`.
- `analyzer/`, `analyst/`, `bench/`, `ccsim/` are owned elsewhere. The frontend code imports from them but never edits them.

## 1. Architecture map

```
Browser (web/, Vite + React 18 + TS, static build)
  main.tsx → App.tsx → shell/AppShell (sidebar, header, routes #/c/<id>) → ThreadView (windowed list)
     ├─ components/Composer          question box, Stop, offline-aware send
     ├─ components/Message + answer/ assistant turn: step timeline, text (Streamdown), table, chart, code,
     │                               callouts, clarify form, error cards, action row (copy, vote, regenerate)
     ├─ components/ArtifactPanel     side panel for table / chart / SQL / code
     └─ lib/useChat                  turn state machine: send, stop, regenerate, edit-and-branch, resume
          │  reduceV1(view, event)   web/src/api/contract.ts (pure reducer over contract events)
          ▼
     adapters/http.ts  HttpAnalystAdapter: POST /api/chat → SSE; Last-Event-ID re-attach; Stop; 429/5xx/offline
     adapters/httpStore.ts  conversation list / detail / rename / delete / pin / archive
     adapters/mock.ts  offline fixture replay (VITE_ANALYST_BASE unset)
     auth/session.ts   demo bearer (VITE_ANALYST_DEMO_TOKEN) or httpOnly cookie (VITE_AUTH=cookie)
          │
          ▼  HTTPS, same origin, /api/*
API (api/, FastAPI, python -m api)
  app.py          routes: /api/chat (SSE), /api/chat/{id}/stream (re-attach), /api/chat/{id}/stop,
                  /api/conversations*, /api/download/{fileId}, /api/capabilities, /api/feedback, /api/auth/sign-out
  backends.py     AuthBackend: DemoAuth (demo:<customer>:<user>), SimAuth (ccsim Cognito → QueryPathOpener)
  live.py         LiveTap: wraps the LLM, turns model/tool calls into step.* / reasoning.* / escalation events
  mapping.py      AnalyzerAnswer → parts (text, table, chart, callouts, clarify, abstain)
  stream_parts.py parts → text.* / table.* / part events
  contract/       versioned JSON schema, conformance rules, golden examples (Contract team)
  analyzer_adapter.py, reasoning_policy.py, strategy_config.py, analyst_shim.py   the real-analyzer bridge (§5)
  security.py, redact.py, sql_mask.py, cancel.py, store.py, transcripts.py, export.py, feedback.py
          │
          ▼
analyzer/ (Analyzer.answer, AnalyzerConfig, opener.QueryPathOpener) → ccsim.query.open_session (prod: STS +
manifest + per-relation secrets; sem.* only)
```

Longer docs: `docs/frontend.md` (web app, PHI rules, stream handling, memory), `docs/frontend-shell.md`,
`docs/frontend_security.md`, `docs/frontend_a11y_perf.md`, `docs/frontend_ci.md`, `docs/frontend_deploy.md`,
`docs/frontend_transcripts.md`, `docs/frontend-analyzer-adapter.md`, `api/CONTRACT.md`.

## 2. Run it

```bash
# API, demo mode (scripted model, synthetic DuckDB, no paid API)
python -m venv .venv && . .venv/bin/activate
pip install -e . && pip install -r api/requirements.txt
python -m api --port 8787

# Web, against that API
cd web && npm ci
VITE_ANALYST_BASE=/ VITE_ANALYST_DEMO_TOKEN=demo:acme_labs:ana@example.test npx vite --port 5174
# (VITE_ANALYST_BASE unset = fully offline mock adapter, no API needed)
```

The Vite dev server proxies `/api` to `CC_API_URL` (default `http://127.0.0.1:8787`). Demo customers are
`acme_labs` and `acme_diagnostics` (`api/backends.py: DEMO_DIRECTORY`); a different customer in the token is a 401.

## 3. Test it

| What | Command | Notes |
|---|---|---|
| Everything the CI runs | `cd web && npm run ci` | `web/ci/run_local.sh`: licences/static checks, oxlint, audit, tsc, vitest, build, SRI/CSP/header checks, Playwright, `pytest api/tests`. Summary in `web/test-results/ci-local.md`. Args: `[web port]`, env `CI_API_PORT`. |
| Unit (web) | `cd web && npx vitest run --maxWorkers=2` | jsdom |
| Lint / types | `npm run lint` (oxlint, `--max-warnings 0`), `npx tsc -b` | |
| E2E | `npm run e2e` (desktop + mobile), `e2e:chaos`, `e2e:xb` (Firefox/WebKit), `e2e:perf` | always `--workers=1` |
| API | `python -m pytest api/tests -q` | about 840 tests, about 5 min |
| Contract conformance | in `pytest api/tests` and `web/src/contract/*.test.ts` | every stream must satisfy `api.contract.conformance.check(events) == []` |

On a shared or small machine, wrap heavy runs: `npm run guarded -- <cmd>` (`scripts/memguard.mjs`: 2 box-wide slots,
at least 3 GB free; see `docs/frontend.md#box-memory-guard`).

### 3.1 CI on GitHub

The workflow is checked in at **`web/ci/web.yml`**, not `.github/workflows/`, because the sprint token had no
`workflow` scope. Moving it is a Peter-only item (§7). Until then CI is the local `npm run ci`.

### 3.2 Contract

`api/CONTRACT.md` is the source of truth (SSE events, parts, errors, reconnect §8, capabilities, BACKEND-FIT §2.1,
changelog). `python -m api.contract.gen_ts` regenerates `web/src/contract/v1.gen.ts`; `web/src/api/contract.ts` is pinned
to it at compile time. The client ignores unknown events and fields (forward-compatible).

### 3.3 BACKEND-FIT guarantees (tested)

- Identity: the customer is the token's `custom:customer` claim, the user is `email` (else `sub`), the same in
  `SimAuth` (ccsim Cognito → `QueryPathOpener` → `open_session`) and `DemoAuth` (`demo:<customer>:<user>` +
  `DEMO_DIRECTORY`). The demo DuckDB connection holds only the identity's `sem.*` relations, with external access off
  and its configuration locked. Escalation (`lossless.*`) is never granted from chat.
- Requests: any `IdentityScopedKey` (`customer`, `tenant`, `schema`, `database`, `relations`, `scope`, `escalate`, …)
  in the body or `options` is 422 `validation` and opens no session. Headers and query parameters are never read for
  scope. Other unknown keys are ignored.
- Client: the web app sends only `{conversationId, messages, resume?, regenerate?, options?, temporary?}`, options
  only `strategy`/`model`, no query string, and only auth/CSRF/idempotency/Last-Event-ID headers.
- Tests: `api/tests/test_backend_fit.py`, `api/tests/test_backend_fit_121.py` (contract), `web/src/adapters/http.backendFit.test.ts`,
  `web/e2e/backend-fit.spec.ts` (no data-source control anywhere in the UI).

## 4. Event contract in one paragraph

`POST /api/chat` returns `text/event-stream`, one JSON event per `data:` line, each with a per-message `seq`. A turn is
`message.start` → any of `status`, `step.*` (explore, sql_running, sql_result, repair_attempt, check, candidates),
`escalation`, `reasoning.delta/end`, `text.start/delta/end`, `table.*`, `part` (chart, code, callout, clarify,
error, …), `heartbeat` (every 15 s) → `message.end {finishReason, usage, sources?, conversationTitle?}`. A dropped
connection re-attaches with `Last-Event-ID: <messageId>:<seq>` and gets only what it missed; if the run is gone the
server answers `stream_gone` and the client reloads the saved answer. Stop is `POST /api/chat/{messageId}/stop`
(interrupts a running query within about 1 s). Full detail: `api/CONTRACT.md`.

## 5. Connecting the real analyzer

<!-- SECTION: contract (analyzer adapter). The Contract team owns this section and replaces this placeholder. -->

*Until the Contract team's section lands, this is the summary of `docs/frontend-analyzer-adapter.md` (read it in full
before wiring):*

- **Today:** `POST /api/chat` already runs the full pipeline (`LiveTap` → `mapping.outcome_parts` →
  `stream_parts.emit_parts`) with `ScriptedLLM`. Wiring the real analyzer means changing **only the LLM object** given
  to `LiveTap`, plus the analyzer config. `api/app.py` deliberately does not import the adapter yet (a test enforces
  this; that test changes in the wiring PR).
- **Paid-API switch (R3):** `api.analyzer_adapter.build_real_llm(kind, enable=...)` returns the analyzer's
  `AnthropicLLM`/`BedrockLLM(allow_paid_api=True)` only if `CC_API_ALLOW_PAID_API` is exactly
  `i-understand-this-calls-a-paid-model-api` **and** `enable=True`. Otherwise it raises `PaidAPIDisabled`, which the
  API shows as "Paid model APIs are disabled on this server".
- **PHI in reasoning (R2):** wire through `run_to_events(..., reasoning="drop")` (the default). Raw model text is
  never streamed. `"summary"` builds a labels-and-counts summary only. There is no raw mode.
- **Shown SQL:** literals are masked by `api.sql_mask` (`CC_API_SQL_LITERAL_MASKING=phi`, never `off`).
- **Strategy (R4):** `api.strategy_config.strategy_config(strategy, model, customer=principal.customer).overrides`
  → `AnalyzerConfig(**{**settings.analyzer_overrides, **overrides})`. The ids come from `GET /api/capabilities`.
- **Stop during SQL (R6):** pass the request's opener through `api.cancel.CancellableOpener`. The live
  `SessionHandle.con` must expose `interrupt()` or `cancel()`.
- **Sessions:** per request from the user's Cognito token via `analyzer.opener.QueryPathOpener` →
  `ccsim.query.open_session` (production does the same with the real AWS). Chat never escalates
  (`NoEscalateOpener`).
- **Rollout order:** (1) wiring with `reasoning="drop"`; (2) `Settings.llm in ("anthropic","bedrock")` →
  `build_real_llm(settings.llm, enable=settings.allow_paid_api)`, demo default stays `scripted`; (3) strategy
  overrides; (4) shadow runs on recordings, gated on `check() == []`.

<!-- /SECTION: contract -->

## 6. Production checklist

Full list with owners: **`docs/frontend_deploy.md`** (`api/tests/test_deploy_doc.py` keeps it in sync with every
`CC_*` variable). The short version:

1. `CC_API_AUTH` set to the production mode with an explicit `auth=` backend (`create_app()` refuses otherwise).
2. Web build with `VITE_AUTH=cookie` and `VITE_SIGN_IN_URL`, **without** `VITE_ANALYST_DEMO_TOKEN`. Cookie
   `cc_session` is `HttpOnly; Secure; SameSite=Strict; Path=/api`. Writes carry `x-cc-csrf: 1`. No CORS.
3. Every HTML response gets a fresh CSP nonce for `__CC_CSP_NONCE__` and the full header set
   (`docs/frontend_security.md`). Check with `web/scripts/header_check.mjs` and `csp_check.mjs`.
4. `/assets/*` immutable, deployed atomically with `index.html`, never rewritten (SRI). Run
   `node web/scripts/sri_check.mjs` after each deploy.
5. Logs: run through `create_app()` (route-shaped paths, truncated IPs). Edge/LB logs and APM scrub URLs and bodies.
6. `CC_API_LLM=scripted` and `CC_API_ALLOW_PAID_API` unset until the BAA and model sign-off.
7. `CC_API_SQL_LITERAL_MASKING` is `phi` or `all`. `CC_TRANSCRIPT_MODE` and the key storage are as decided in
   `docs/frontend_transcripts.md`.
8. The heartbeat (15 s) stays below every proxy idle timeout. `text/event-stream` is not buffered
   (`proxy_buffering off`).
9. One uvicorn worker per container plus sticky routing (§7), or move live runs to a shared store.

## 7. Peter-only items (need credentials or infra the sprint didn't have)

- [ ] **Move CI into place:** `git mv web/ci/web.yml .github/workflows/web.yml` (needs a token with `workflow`
      scope). See `docs/frontend_ci.md`.
- [ ] **Cognito revoke on sign-out:** the production `AuthBackend.sign_out(token)` calls `GlobalSignOut` /
      `AdminUserGlobalSignOut`, is idempotent, never raises and never logs the token. Keep access tokens short-lived
      (15 min or less). Test by replaying a copied cookie after sign-out and expecting a 401
      (`docs/frontend_deploy.md` §1).
- [ ] **CDN `/assets/` rule:** no minify, Rocket Loader, HTML optimisation or injection on this origin. Immutable
      caching for `/assets/*`, `no-store` for everything else (§2 there).
- [ ] **Sticky routing:** live runs live in process memory, so re-attach and the 3-runs-per-user cap need one worker
      per container plus sticky routing by cookie or user (§9 there).
- [ ] **Timeouts:** proxy/LB idle timeout over 15 s (ALB default 60 s is fine). The client treats 45 s of silence as
      a stalled stream and re-attaches.
- [ ] Paid-API and model sign-off (BAA) before setting `CC_API_ALLOW_PAID_API`. The transcript retention decision.

## 8. Removed and excluded features (do not reintroduce)

| Feature | Status | Why / decision |
|---|---|---|
| Sharing (share button, dialog, `/shared/*` view, share endpoints, registry, capabilities, examples, docs) | **Removed** (Peter, 12:15 AM 2026-10-26) | Never reintroduce. `/share/*` URLs are a plain 404. Removed with a contract bump (see `docs/frontend-contract-migration.md`). |
| Data-source / DB / schema / scope picker (composer "Claims lake · sem.*" chip, ScopePopover, header Scope row) | **Removed** (Peter, 11:57 PM) | Scope comes from the identity (§3.3). Read-only context from the session (for example the organization in the account menu) is fine. |
| Read aloud (browser speech) | Excluded (12:11 AM) | |
| Extra keyboard shortcuts beyond the existing ones | Excluded (12:11 AM) | |
| Contrast / larger or dyslexia font / reduce-motion **settings** | Excluded (12:11 AM) | Existing a11y compliance stays (OS reduced-motion and contrast are honoured; `docs/frontend_a11y_perf.md`). |
| Archive-all / delete-all, bulk select | Excluded (12:11 AM) | |
| Usage-limit warnings, Usage page | Excluded (12:11 AM) | |
| "Answer now" while thinking | Excluded (12:11 AM) | |
| Editable plan preview before long analyses | Excluded (12:11 AM) | |
| Anything needing `analyzer/` changes, paid LLM calls, infra the backend lacks, or that breaks identity-scoped data / PHI rules | Excluded (12:11 AM) | Log ideas under BACKEND-FIT in the sprint NOTES instead of building them. |
| File upload / attachments | Not built | PHI. The analyzer reads only the identity's `sem.*` relations. |

<!-- SECTION: not-built. Teams list in-flight features dropped at the 12:20 AM finalize (multi-question clarify form,
A30 composer reply, collapsed thinking, context-full state, quote-a-selection, suggested follow-ups) as built / not built. -->

Shell, at the 12:20 AM finalize:

- A30 composer reply: **built** (#295). With a clarifying question open, the composer placeholder is "Reply…" and the
  typed text answers it (`resume` on the wire).
- Branch in new chat (answer More menu): **built** (#294).
- Chat-length-limit (context-full) read-only state with "Start a new chat" / "Branch in new chat": **not built**.
- Quote-a-selection chip in the composer: **not built**.
- Response preferences settings section: **not built**.
- ChatGPT-style single-row pill composer: **not built** (prototype dropped).

## 9. Team sections

<!-- SECTION: shell. Shell team: layout, sidebar, routes, composer, themes, shortcuts, removed share/scope UI. -->

### 9.x Shell (layout, sidebar, routes, composer, settings, themes, shortcuts)

Owner during the sprint: shell. Design rule: ChatGPT/Claude parity from observation only, calm and neutral (one accent,
hairline borders, ~48rem column, small muted icons, fast quiet motion). `src/shell/slopGuard.test.ts` fails the build
on gradients, glass/blur, glow, heavy shadows and similar.

**Components map** (`web/src/`)

| File | What it owns |
|---|---|
| `App.tsx` | Path route switch: app, `/signed-out`, everything else is the 404 (`shell/StatusPage`). |
| `shell/routes.ts` | `routeFromLocation` (path routes) and `signedOutUrl`. |
| `shell/AppShell.tsx` | Layout and wiring: sidebar + header + `ThreadView` + docked composer; hash route `#/c/<id>`; dialogs (search, settings, shortcuts, delete/rename); global shortcuts; temporary chat; A30 composer-as-reply (`lib/openClarification.ts`); branch in new chat. |
| `shell/Header.tsx` | Product menu (strategy / model from `/api/capabilities`), title menu (Rename, Copy as Markdown, Export as Markdown, Export as JSON when the server exports, Delete), temporary-chat toggle, new chat. No share, no scope. |
| `components/Sidebar.tsx` | New chat, Search chats, date-grouped chats with a row menu (rename, pin, archive, export, delete), filter past 20 rows, collapsed rail with avatar, account menu (identity read-only, Settings, Sign out). Mobile: a drawer with swipe. |
| `components/Composer.tsx` | `+` (attach text files) / textarea / send. Enter sends, Shift+Enter new line, long pastes become chips, Stop while streaming, offline-aware. Placeholder becomes "Reply…" when a clarifying question is open. |
| `shell/ThreadView.tsx` | Windowed message list, scroll-to-bottom, stays pinned while streaming. |
| `shell/SearchDialog.tsx` | Ctrl/⌘K chat search (server search when the API has it). |
| `shell/SettingsDialog.tsx` | Settings modal (see below). |
| `shell/StarterCards.tsx` | Empty-state heading and starter prompts. |
| `shell/StatusPage.tsx` | 404 and signed-out / session-expired pages. |
| `shell/OfflineBanner.tsx` | Offline notice. |
| `shell/shortcuts.ts` | One table drives the key handler docs and the Shortcuts sheet. |
| `shell/primitives.tsx` | Menu, Dialog, IconButton, `useMenu`, `modKey`. |

**Routes**

- `/` (the base): the app. Chats are hash routes `#/c/<id>`; an empty hash is a new chat.
- `/signed-out` and `/signed-out?reason=expired`: signed-out and session-expired pages.
- Anything else, including `/shared/*` and `/share/*`: the plain 404 ("Page not found", link home).
- `document.title` never contains a chat title (PHI sink; `src/security/titleSink.test.tsx`).

**Settings** (modal, tabs; ChatGPT and Claude use a full page, we kept the modal)

- General: Theme (System / Light / Dark), Style (palette), Send with Enter.
- Data controls: Chat history (on/off; off = memory only), Archived chats (unarchive), Export chats, Unsent drafts, Storage.
- Shortcuts (desktop only): New chat ⌘/Ctrl⇧O, Search ⌘/Ctrl K, Toggle sidebar ⌘/Ctrl⇧S, Focus input ⇧Esc, Copy last
  response ⌘/Ctrl⇧C, Export as Markdown ⌘/Ctrl⇧E, Settings ⌘/Ctrl ,, Shortcuts ⌘/Ctrl /, Stop Esc, Slash commands /,
  New line ⇧Enter, Close Esc.

**Known gaps (shell)**

- Esc stops a running answer (Claude does this; ChatGPT does not). Kept on purpose.
- Composer is a two-row box. ChatGPT's single-row pill that grows on Shift+Enter was prototyped and dropped at the
  finalize (patch kept outside the repo).
- The empty-state heading is larger than ChatGPT's.
- Account identity is a demo constant (`ACCOUNT` in Sidebar); production should read it from the session (`/api/me`).
- The `+` attach button adds text files to the question as context; files are not queryable data (see §8 "File upload").
- Strategy / model in the product menu are validated and echoed by the API but don't change the scripted run (§10).
- The header's "Data: Live API / Demo data" row is informational only.
- Settings is a modal, not a full page.

<!-- SECTION: answer. Answer team: answer rendering (timeline, text, table, chart, clarify, errors), print, copy. -->

### 9.1 Platform (adapters, streaming, performance, security plumbing)

- **Streaming:** `adapters/http.ts` streams SSE through `guardBody` (45 s idle → stalled; 32 MiB per-frame cap),
  de-duplicates replayed events by `seq`, and re-attaches with Last-Event-ID (backoff 250 ms, 1 s, 2 s, 4 s). Offline
  waits without spending retries and gets one free try per real offline period. 429 honours Retry-After with a
  visible countdown. 5xx mid-answer re-attaches. `stream_gone` reloads the saved answer.
- **Calm reconnect UX (like ChatGPT/Claude):** a drop that is back within 1 s (`RECONNECT_QUIET_MS`) shows and
  announces nothing. A longer one shows "Reconnecting…" under the answer, and the server status returns when the
  replay resumes. Going offline shows "Offline, waiting for the network…" and the settled offline banner. If the
  retries run out, the partial answer stays with a retryable "Connection lost" card. Screen readers hear transport
  changes through one throttled polite live region. Tests: `lib/useChat.connection.test.tsx`,
  `adapters/http.{midstream,flapping,ratelimit,streamgone,guard}.test.ts`, `e2e/chaos.spec.ts`.
- **Non-stream calls:** `lib/fetchGuard.ts` (429 retry, capped JSON bodies).
- **Clock skew:** `lib/serverClock.ts` (server `Date` offset for expiries and Retry-After).
- **Performance:** idle prefetch of the Markdown, KaTeX and Vega chunks; windowed thread; 200-turn memory test
  (`e2e/memory200.spec.ts`); Lighthouse budget (`npm run check:perf`); CPU-throttle audit. See `docs/frontend.md`,
  `docs/frontend_a11y_perf.md`.
- **Security plumbing:** CSP nonce, Trusted Types, SRI with import map, no-store, `prefs.ts` as the only `localStorage`
  writer (allowlisted keys), fixed `document.title`. See `docs/frontend_security.md`.

## 10. Known limitations

- Live runs are in process memory (re-attach and the run cap are per process; §7 sticky routing).
- The demo has one relation (`sem.claims`), a subset of production `SEM_TABLES`. Demo answers are scripted, not
  analyzed.
- `api/app.py` is not wired to the real analyzer (§5). Strategy and model options are validated and echoed but
  don't change the scripted run.
- a05 verifier-agent steps need an executor tap (adapter doc §6, open).
- CSV filenames use the UTC day (`docs/frontend_deploy.md` §10).
- CI runs locally until the workflow move (§7).
- Transcript storage is `ephemeral` by default. The persistent mode needs the retention decision and KMS keys.

<!-- SECTION: limitations. Any team: append known limitations in your area. -->

## 11. Final QA score

<!-- SECTION: qa. QA team: final score, method, open findings, and how to re-run the audit. -->
