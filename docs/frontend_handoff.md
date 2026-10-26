# CC frontend: handoff

**Owner: platform.** Other teams add their own sections in the marked slots below (contract: §5 analyzer adapter;
shell, answer: §9; QA: §11). This file is meant to be enough for someone with **no other context** (for example
Claude, picking up the repo) to run, test, ship and connect the real analyzer. Where a topic has a longer doc, this
file says what you need and links the rest.

State at handoff: branch `grokbot/frontend-sota`, contract version in `api/contract/__init__.py: CONTRACT_VERSION`
(**1.23.0** at handoff: 1.21 added BACKEND-FIT, 1.22 removed sharing, 1.23 added multi-question clarify, which is additive). Everything runs offline in **demo mode**:
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

<!-- SECTION: contract (analyzer adapter). Owner: contract team. Contract 1.22.0 on this branch; 1.23.0 (multi-question clarify) is additive and does not change the wiring. -->

**Status:** the adapter exists and is tested from fixtures only. It is **not wired**: `api/app.py` never imports it,
and a test (`api/tests/test_analyzer_adapter.py`) enforces that. No paid API has ever been called. Nothing under
`analyzer/`, `analyst/`, `bench/` or `ccsim/` was changed. The full spec is in
[`frontend-analyzer-adapter.md`](frontend-analyzer-adapter.md). This section is the how-to.

**5.1 What already runs the real code path.** `POST /api/chat` already drives the real `analyzer.loop.Analyzer`
against a real session opener. Only the LLM is scripted (`ScriptedLLM`, `api/backends.py: make_llm`). Every model call
goes through `api.live.LiveTap`, which turns calls and tool results into `step.*` / `status` / `escalation` events.
`api.mapping.outcome_parts` turns the `AnalyzerAnswer` into parts, and `api.stream_parts.emit_parts` streams them.
Connecting the real analyzer therefore means **swapping the LLM object and applying config**. The event mapping does
not change.

**5.2 Functions (all in `api/`)**

| function | file | role |
|---|---|---|
| `build_real_llm(kind, *, enable)` | `analyzer_adapter.py` | `AnthropicLLM` / `BedrockLLM(allow_paid_api=True)`, but only if `enable=True` **and** the env switch is set; otherwise raises `PaidAPIDisabled` |
| `paid_api_enabled(env=None)` | `analyzer_adapter.py` | true only when `CC_API_ALLOW_PAID_API` is the exact phrase |
| `run_to_events(question, run, inner_llm, *, model, reasoning="drop", sql_masking="phi", cancelled, principal, downloads, strategy)` | `analyzer_adapter.py` | the full framed stream (`message.start` … `message.end`) for one run; wraps `emit` in the reasoning policy and SQL masking |
| `adapter_strategy(strategy, *, multi_candidate, escalated)` | `analyzer_adapter.py` | the capabilities id for `message.start.strategy` |
| `answer_from_bench(answer, *, columns)` | `analyzer_adapter.py` | `analyst/` (`bench.agents.Answer`) → `AnalyzerAnswer` |
| `events_for_recording(recording)` | `analyzer_adapter.py` | replays a recorded real run with no model, network or DB (CI) |
| `strategy_config(strategy, model, *, customer, grid=None, strict=False)` | `strategy_config.py` | strategy/model (and optional grid cell) → `AnalyzerConfig` overrides + fidelity notes |
| `ToolLoopShim`, `TextRoleShim`, `SingleShotShim` | `analyst_shim.py` | adapt the `analyst/` agents' model interfaces onto `LiveTap` |
| `CancellableOpener(opener, cancelled)` | `cancel.py` | Stop interrupts the running SQL; already used by `/api/chat` |
| `SimAuth(aws, deployment)` | `backends.py` | Cognito id token → `Principal(custom:customer, email)`; sessions only through `ccsim.query.open_session` (`QueryPathOpener`) wrapped in `NoEscalateOpener` |

**5.3 The wiring PR (one reviewed PR; about 20 lines in `api/app.py`)**
1. In `create_app`, when `settings.llm in ("anthropic", "bedrock")`, build the model with
   `build_real_llm(settings.llm, enable=True)` instead of `make_llm(...)`. Keep `scripted` as the default. The test
   that forbids importing the adapter from `api/app.py` changes in the same PR.
2. Config: `AnalyzerConfig(**{**settings.analyzer_overrides, **strategy_config(body.options.get("strategy",
   DEFAULT_STRATEGY), body.options.get("model", "auto"), customer=ctx.principal.customer).overrides})`. This
   replaces the demo-only `SCENARIO_CONFIG` merge. Drop `scenario` for real runs: it is a demo knob.
3. Wrap `emit` with `ReasoningPolicy("drop")` (add a setting if you want `summary`; no env var exists yet) (`api/reasoning_policy.py`), as `run_to_events`
   does. The demo path doesn't need it (scripted text is synthetic), but a real model's text can quote rows.
4. Keep `CancellableOpener`, `NoEscalateOpener`, `mask_event`/`mask_part` and the audit allowlist exactly as they are.
5. Auth: `create_app(auth=SimAuth(aws, deployment))` with `CC_API_AUTH` set to something other than `demo`.
   `create_app` refuses to start without an explicit backend in that case. The data scope comes **only** from the
   token (`custom:customer` → `open_session` → the manifest's allowed relations). The request can't choose it:
   `IdentityScopedKey` keys are rejected with 422 (CONTRACT §2.1).

**5.4 The paid switch (two locks, both required)**
- Deployment: `CC_API_ALLOW_PAID_API=i-understand-this-calls-a-paid-model-api` (that exact string; anything else
  counts as off).
- Code: `build_real_llm(..., enable=True)`.
- If either is missing, the run ends with an `error` part ("Paid model APIs are disabled on this server",
  `finishReason: "error"`) and **no request leaves the box**. `CC_API_LLM=anthropic` on its own is safe: today's
  `make_llm` builds `AnthropicLLM()` without `allow_paid_api`, which raises `PaidAPIDisabled` on the first call.

**5.5 Environment variables**

| var | default | meaning |
|---|---|---|
| `CC_API_LLM` | `scripted` | `scripted` (offline demo) / `anthropic` / `bedrock`; `/api/health` reports it, and `Capabilities.demo` is true only for `scripted` |
| `CC_API_ALLOW_PAID_API` | unset | the paid switch above |
| `CC_API_AUTH` | `demo` | anything else requires `create_app(auth=SimAuth(...))` |
| `CC_API_MODE` | — | **mentioned in docstrings but not read by any code.** Sim/prod mode is selected only by passing `auth=SimAuth(...)` to `create_app` (with `CC_API_AUTH` ≠ `demo`) |
| `CC_API_SQL_LITERAL_MASKING` | `phi` | shown-SQL literal masking: `phi` / `all` / `off`; an unknown value means `phi` |
| `CC_API_ORG_POLICY` | demo policy (JSON, same shape as `capabilities.ORG_POLICY`) | per-org disabled strategies/models (same rule as the 422 and `strategy_config`) |
| `CC_API_MAX_CONCURRENT_RUNS` | `3` | per-user run cap |
| `CC_API_TEMPORARY_TTL_SECONDS` | `3600` | temporary chat TTL; `≤0` turns it off |
| `CC_API_DEMO_PACE`, `CC_API_MODEL_DELAY_SECONDS`, `CC_API_STREAM_DELAY_SECONDS` | demo pacing | **set `CC_API_DEMO_PACE=0` for real models** (pacing applies only to `scripted`, but be explicit) |
| `CC_API_HEARTBEAT_SECONDS`, `CC_API_DISCONNECT_GRACE_SECONDS`, `CC_API_MAX_BODY_BYTES` | 15 / 10 / cap | transport |

The complete list, enforced by `api/tests/test_deploy_doc.py`, is `docs/frontend_deploy.md` §8.

**5.6 Strategy mapping (`capabilities.STRATEGIES[*].config` → `AnalyzerConfig`)**

| capabilities id | label | overrides |
|---|---|---|
| `tool_loop` (default) | Standard | `{}` (base: glossary basic, no dictionary/profile, row_limit 5000, candidates 1, cascade off) |
| `cascade` | Escalate when stuck | `cascade: "haiku_then_sonnet"` |
| `candidates` | Cross-check | `candidates: 3, candidate_selection: "execution_vote"` |

A non-`auto` model adds `model`. An org-disabled or unknown strategy/model raises `ValueError`, matching the API's
422. The optional 384-grid cell mapping and its fidelity flags (`multiagent` and `decompose_cte` are
**unsupported**) are in the adapter spec §3 R4. `message.start.strategy` is always a capabilities id
(`adapter_strategy`).

**5.7 PHI defaults (keep them all)**
- Reasoning: `drop`. No model text reaches the client. `summary` is opt-in and built only from labels and counts,
  then checked with `phi_findings()` (fail closed). There is no raw mode.
- Shown SQL: `phi` masking (literals compared with PHI-bearing columns become `?`, plus `maskedLiterals`). The
  executed SQL is unchanged.
- `step.sql_result` carries column names and counts only, never row values. Error text goes through `scrub()`.
- Audit: an allowlist of fields only (no SQL, rows or question text). Access logs record route shapes, and IPs are
  truncated.
- Escalation (`lossless.*`) is never granted from chat (`NoEscalateOpener`).
- Live multi-question clarify needs an analyzer `ask` spec change (`analyzer/tools.py` has only `question`).
  Until then, live clarifications are single-question. This is logged under BACKEND-FIT in NOTES.

**5.8 How to verify (in order; the first three are free)**
1. `pytest api/tests/test_analyzer_adapter.py api/tests/test_adapter_r2_r4_r7.py api/tests/test_adapter_strategy_and_timing.py`: 9 recorded shapes plus
   synthetic-PHI runs are schema-valid, conformant (`api.contract.conformance.check(events) == []`) and PHI-free.
2. Record a real run in a sandbox (wrap the LLM so each `complete()` appends `{model, candidate_index,
   tool_results, completion}`), **scrub row values**, add it under `api/tests/fixtures/analyzer_runs/`, and run
   `events_for_recording` in CI.
3. Full gates: `pytest api/tests`, `python -m api.contract.gen_ts --check`, `python -m api.contract.make_plot_cases --check`,
   and in `web/` run tsc, oxlint, vitest and build.
4. A paid smoke test needs Peter's sign-off: in a non-prod deployment set the switch, send one synthetic question per
   strategy, and confirm `/api/health` shows the LLM, that the stream passes `check()`, that there is no
   `reasoning.delta` under `drop`, that Stop returns `aborted` within 1 s during SQL, and that the audit log has no
   SQL or row values.
5. Shadow runs: replay recorded questions and gate on `check() == []` and on the PHI scan before turning it on for users.

**5.9 Dev and mock runs with the shipped analyzer (claude -p, no API key)**
`CC_API_ANALYZER_INTERACTION` picks how the analyzer reaches its model: `model_api` (default: the in-process loop
above, client from `CC_API_LLM`) or `claude_code_cli` (`api/claude_code_cli_driver.py`): bundle B1 (Haiku, effort low,
playbook v1, invariants glossary, dictionary with derivations, enum profile, value lookup, 3 BM25 TRAIN examples, free
SQL over `sem.*`, one candidate, no repair) run by local headless Claude Code exactly as the benchmark runs it, on the
signed-in user's subscription. Steps stream from the session's calls.jsonl, Stop kills the worker, the final SQL is
re-executed in the sandbox. With `DemoAuth` the data is the bench snapshot; with `SimAuth` (`python -m api.sim_server
--world-dir DIR --tokens-file FILE`: ccsim Cognito/STS/S3 with acme_labs and acme_diagnostics) every command runs in the
API process against the signed-in customer's reader session (`QueryPathOpener`, opened once per run), reached by the
worker's `analyzer.cli` over a run-scoped unix socket with a one-run capability (`analyzer/run_channel.py`): no
credential reaches the worker or the disk, and the customer comes only from the token. Other auth backends are refused,
as are any strategy/model option but the defaults and the `escalate` command. No live clarification: B1 has `ask`
off, and multi-question clarify still needs the analyzer `ask` spec change (5.7).

```bash
CC_API_ANALYZER_INTERACTION=claude_code_cli CC_API_ANALYZER_SNAPSHOT=.ccsim/bench/<snapshot id> \
CC_TRANSCRIPT_MODE=persisted CC_TRANSCRIPT_DIR=/tmp/cc-chat/blobs CC_TRANSCRIPT_KEYS_DIR=/tmp/cc-chat/keys \
CC_API_DEMO_PACE=0 python -m api --port 8787
```

Persistence (no DynamoDB): in persisted mode each conversation is one sealed object updated by read, change and
`If-Match` write (retried when another task wrote first), and each run's events go in numbered write-once chunk
objects, so `Last-Event-ID` replays a run on any API task or after a restart. `CC_TRANSCRIPT_S3_BUCKET` puts both on
S3 (`S3Blobs`, tested on ccsim's S3); `CC_TRANSCRIPT_DIR` is the same on disk.

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
| Sharing (share button, dialog, `/shared/*` view, share endpoints, registry, capabilities, examples, docs) | **Removed** (Peter, 12:15 AM 2026-10-26; contract 1.22.0, shell #302) | Never reintroduce. `/share/*` URLs are a plain 404. See `api/CONTRACT.md` §17 and `docs/frontend-contract-migration.md`. Ratchet: `web/e2e/share-removed.spec.ts`. |
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

### 8.1 Finalize (12:20 AM): what was finished and what was dropped

| Item | Status | Notes |
|---|---|---|
| Multi-question clarify form (Claude-style: 1–4 questions, options, Other, Skip) | Built (answer UI; contract 1.23.0, `web/src/contract/clarify.ts`) | **Demo-only** (`clarify_multi` scenario) until the analyzer's `ask` tool carries `questions`; the adapter passes them through unchanged. |
| A30: reply to a clarification from the composer | Built (#295) | The card hints "Or type your answer below". |
| Collapsed thinking disclosure | Built (answer) | Reasoning is dropped by default on the real path (§5, R2), so it shows only when steps or a summary exist. |
| Suggested follow-ups | Built, provisional (`follow_ups` part) | Treat it as provisional until the contract pins the part. |
| "Ask to change response…" | Built (answer) | Sends the instruction as the next turn (the contract has no regenerate instruction). |
| Chart Copy as image + click a bar to ask about it | Built (answer) | Copy as image appears only where the browser has an image clipboard. The bar follow-up is offered only on the live turn. |
| Context-full / chat-length-limit state | **Not built** | No error code was defined. |
| Quote-a-selection | **Not built** | The draft was discarded. |
| Response preferences | **Not built** | |
| ChatGPT pill-shaped composer | **Not built** | The patch was kept outside the repo. |
| Accessibility *settings* (gap #13) | **Not built** (excluded) | Existing a11y compliance stays. |

<!-- SECTION: not-built (teams: correct the table above if your item's status changes). -->

Shell, at the 12:20 AM finalize:

- A30 composer reply: **built** (#295). With a clarifying question open, the composer placeholder is "Reply…" and the
  typed text answers it (`resume` on the wire).
- Branch in new chat (answer More menu): **built** (#294).
- Chat-length-limit (context-full) read-only state with "Start a new chat" / "Branch in new chat": **not built**.
- Quote-a-selection chip in the composer: **not built**.
- Response preferences settings section: **not built**.
- ChatGPT-style single-row pill composer: **not built** (prototype dropped).

Contract, at the 12:20 AM finalize:

- Multi-question clarification (Claude-style: 1–4 questions, 12-character headers, 2–4 described options,
  multi-select, Other, Skip): **built in the contract** as 1.23.0 (`ClarificationPart.questions`,
  `resume.answers`, demo scenario `clarify_multi`, helpers `web/src/contract/clarify.ts`). Demo only: live needs the
  analyzer `ask` spec to carry `questions` (BACKEND-FIT). The answer team renders the form.
- Context-full error code: **not built** (not started at the finalize).
- Suggested follow-ups and response preferences: **not built** (dropped at the finalize).

## 9. Team sections

<!-- SECTION: shell. Shell team: layout, sidebar, routes, composer, themes, shortcuts, removed share/scope UI. -->

### 9.1 Shell (layout, sidebar, routes, composer, settings, themes, shortcuts)

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
- Settings is a modal, not a full page.

<!-- SECTION: answer (§9.2). Answer team: answer rendering (timeline, text, table, chart, clarify, errors), print, copy. -->

### 9.2 Answer (answer rendering: timeline, text, table, chart, clarify, states)

Everything that renders one assistant turn: `web/src/components/Message.tsx` (`AssistantTurn`, `PartView`), `web/src/components/parts/*` (renderers), `web/src/components/answer/*` (timeline, clarify, sources, states, follow-ups), `web/src/lib/{pacedText,tableReveal,frameBatch,contract}.ts`, plus `web/src/render/chartSpec.ts`. Data comes as contract v1 SSE events (`api/CONTRACT.md`, `web/src/contract/v1.gen.ts`) that `reduceV1` folds into an `AssistantViewV1` (`parts[]`, `steps[]`, `usage`, `sources`, `escalations`).

#### Part renderers (`PartView` in Message.tsx)
| part `type` | renderer | notes |
|---|---|---|
| `text` | `parts/Markdown.tsx` (Streamdown, lazy KaTeX/shiki) | paced while streaming (below); prose tables have no outer border, and their tools show only on hover |
| `reasoning` | inside the step timeline (`answer/StepTimeline.tsx`) | dropped server-side by default (PHI policy, `api/reasoning_policy.py`) |
| `table` | `parts/DataTable.tsx` | inline, no outer border/zebra, toolbar on hover; `sqlPartId` → the query is shown under the table (`TableSql`), not as its own block; `rowCount > rows` → preview note + `file` chip |
| `chart` | `parts/ChartView.tsx` (vega-embed, lazy, AST interpreter, remote loads blocked) | strict `plot` (1.18) is validated before drawing; hover toolbar: Copy as image (PNG to clipboard, ✓), PNG/SVG/CSV, expand. Click a bar → a quiet "Ask about X" row that sends a follow-up (only on the live turn) |
| `code` | `parts/CodeBlock.tsx` | icon-only copy that turns into a ✓; `role:'query'` linked to a table is hidden here |
| `image`, `file`, `artifact`, `callout`, `error` | `parts/misc.tsx` | callout `assumption` items with `editable` get "Change" (pre-fills the composer, never names a data source) |
| `clarification` | `answer/States.tsx` `ClarifyCard`, or `answer/ClarifyForm.tsx` when the part has `questions[]` | see Clarify |
| `abstain` | `answer/States.tsx` `AbstainCard` | |
| `follow_ups` (provisional, not in the contract yet) | `answer/FollowUps.tsx` | ≤3 plain rows under the last finished answer; a click sends the text |
| anything else | `Unsupported` chip | forward-compatible |

Turn chrome: Sources chip ("Looked at sem.claims +N"; only when data was actually consulted), action row (copy, 👍/👎 with reasons, Regenerate + style menu incl. "Ask to change response…", which sends the instruction as the next turn), version nav, "Stopped" line (tiny, muted; Retry only if nothing arrived).

#### Streaming and pacing
- **Thinking disclosure** (`StepTimeline.tsx`): collapsed by default. While live it shows one muted line naming the current step (200 ms text fade, no spinner/shimmer/timer). When done it reads "Thought for Ns", and a click expands the steps. It has no live region of its own: the shell's single status region announces "answering…" and "Answer complete" (QA-S16). `prefers-reduced-motion` turns off all animation.
- **Text** (`lib/pacedText.ts`): text that arrives after mount is revealed at backlog×dt/`DRAIN_MS`(200 ms), never slower than 250 chars/s, snapping to word ends. Finished or reopened text shows at once, and reduced motion shows everything at once. There is no caret: the last block's trailing ~3em fades (CSS mask on `.streaming-caret`) until it lands, and an empty turn shows a single pulsing dot.
- **Tables** (`lib/tableReveal.ts`): while streaming, rows appear over ≤24 frames (~400 ms). `table.start/rows` (1.9) and `chart.start/points` (1.18) draw early, and the final `part` with the same id is canonical. If `message.end` comes first, the partial is dropped.
- **Batching** (`lib/frameBatch.ts`): reducer updates paint at most once per animation frame.
- **Stop**: the result is `finishReason:'aborted'`. Content already shown stays.

#### How analyzer output maps to parts (server: `api/mapping.py`, `api/live.py`, `api/stream_parts.py`)
- **Steps:** analyzer tool calls are tapped live (`LiveTap`) and sent as `step.*` events. `live.py:_EXPLORE` maps tools to probe kinds: `list_relations→list_views`, `describe_relation→describe_view`, `sample_rows/lookup_values→sample_values`, `profile`, `lookup_glossary/dictionary`, `lookup_examples→search_examples`, `query_metrics→lookup_dictionary`, `request_escalation`. `run_sql` becomes `sql`/`repair` steps with a query. The UI labels them in `answer/normalize.ts`:
  - describe/sample/profile on a schema-qualified relation read "Looked at sem.claims";
  - list_views reads "Found the tables it can read";
  - an explore-only run is summarised as "Looked at a, b +N more".
  - The user never picks a DB, schema or view (BACKEND-FIT), and nothing in the answer implies they did.
- **Outcome** (`outcome_parts`):
  - `answer.kind=="answer"` gives, in order:
    - `text` (streamed as `text.start/delta/end`, ~24-char word chunks);
    - `code` (role `query`, collapsed);
    - `table` (≤50 preview rows, `csvFileId`, `sensitivity` limited/deidentified, `source.views`);
    - `file` if more than 50 rows;
    - a `chart` when `chart_for` finds a time series or categorical breakdown;
    - `callout`s: assumptions (editable), caveats, and `phi_redaction` for PHI columns.
    - finish is `stop`.
  - `clarification` → a `clarification` part (`choices` ≤8, `allowFreeText`), finish `needs_input`. The reply comes back as `resume:{clarificationId, choiceIds?, text?}`, sent from a chip, from the multi-question form, or from the composer (QA-A30, shell).
  - `refusal` → `abstain` (category from `refuse` args, else PHI/out_of_scope), and `abstain` → `abstain` low_confidence; both finish `abstain`.
  - Anything else → `error` (`query_failed`, retryable).
- **Multi-question clarify (Claude model, contract 1.23.0):** a `clarification` part with `questions` renders `answer/ClarifyForm.tsx`:
  - 1–4 questions one at a time, ≤12-char header tabs, 2–4 described options, multi-select, "Other…", Skip, number keys and arrow keys;
  - it docks into `#cc-composer-dock` when the shell provides one, else it sits sticky at the end of the last answer.
  - The reply is `resume: clarifyRequest(id, answers)` (`resume.answers`, one per question; empty = skipped). The visible turn is `clarifySummary` ("Metric: …; Period: no preference"). Both come from `web/src/contract/clarify.ts`.
  - It is demo-only (`clarify_multi`) until the analyzer's `ask` tool emits `questions`; `outcome_parts` passes them through.
- **Follow-ups:** they need a `follow_ups` part from the server. The real analyzer must produce them without an extra paid call; otherwise leave them off and the UI shows nothing.

#### Not built (answer side)
- Quote-a-selection (composer quote chip): dropped at the finalize; the draft was discarded.
- Read aloud, Answer now and plan preview: excluded (Peter, 12:11 AM).
- Shared/read-only rendering (`readOnly`, `sqlWithheld`, "SQL not shared"): removed with sharing.

#### Tests
- **vitest:** `src/components/answer/*.test.tsx` (thinkingDisclosure, backendFit, clarifyForm, followUps, quietStream, answer, sources, edgeScenarios…) and `src/components/parts/*.test.tsx` (chartActions, codeChrome, tableEdge…).
- **e2e:** `e2e/answer-edge.spec.ts`, `visual-states.spec.ts`, `contract-outcomes.spec.ts`.


### 9.3 Platform (adapters, streaming, performance, security plumbing)

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
  (`e2e/memory200.spec.ts`); CPU-throttle audit. Budgets: `web/bundle-budget.json` (initial JS 160 KB gz, CSS 16 KB
  gz, total JS 800 KB gz) and Lighthouse `scripts/perf_budget.json` (`npm run check:perf`: perf ≥ 0.95, a11y 1.00,
  cold-load bytes `load-byte-weight` ≤ 350 KB, bytes including the idle prefetch ≤ 800 KB). Initial CSS sits at about
  15.9/16 KB gz, so new global CSS needs a matching cut. See `docs/frontend.md`,
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

- Multi-question clarify and suggested follow-ups work end to end only in the demo until the analyzer emits them
  (BACKEND-FIT: the analyzer `ask` tool has only `question`).

<!-- SECTION: limitations. Any team: append known limitations in your area. -->

## 11. Final QA score

<!-- SECTION: qa. QA team: final score, method, open findings, and how to re-run the audit. -->

**Overall: 8.71 / 10** across the 12 rubric areas (**8.62** with the 13th "Fidelity / no-slop" area at 7.5).
That is the last scored cycle (C9, 2026-10-25 22:30 PT), and the trend was 6.46 → 8.71 over 8 cycles. The full table and the
cycle log are in `QA_SCORECARD.md`, and the rubric is in `QA_RUBRIC.md` (sprint files under `/workspace/cc_fe/`, not in the repo).
Since C9, the C10 and finalize work fixed findings without re-scoring (S16, S19, A21, A23–A27, S25/S26/S28–S31,
S37, BF1, sharing removal). So 8.71 is a lower bound for the handed-off build.

**Finalize verification** (2026-10-26 01:20–01:50 PT, `grokbot/frontend-sota` @ `112169a`, after answer #312):

| Gate | Result |
|---|---|
| `cd web && npm run ci` (lint, audit, typecheck, vitest, build, bundle/SRI/CSP/header checks, Lighthouse, slow-3G, api pytest, pip-audit) | **PASS** (409 s) |
| `pytest api/tests` (demo, ScriptedLLM) | **899 passed** |
| e2e desktop + mobile (`npm run e2e`, `--retries=0`) | **242/242 passed**, including all visual baselines; nothing stale and no flakes on a single no-retry run |
| Chaos (`npm run e2e:chaos`, 26 tests: stream cut at every event index, malformed streams, HTTP faults, hung server, mash/duplicate sends, reload at 7 stages, multi-tab, 200%/400% zoom, 320 px reflow, RTL, 3 seeded monkeys × 120 actions) | **26/26 passed**, 0 page errors |
| Cross-browser (`npm run e2e:xb`, last run C9) | Firefox 62/67 → its one real bug QA-S19 (focus lost after first send) was fixed in #277 and the rest were harness fixes. WebKit 65/67 (harness-only). iPhone 13 WebKit 41/42 (the one test needs CDP touch, so it is Chromium-only). Not re-run at finalize. |
| Sharing removed (QA-SH0) | No share affordance in the UI, menus, keyboard or mobile. `/share/*`, `/shared/*` and `/api/share/*` are plain 404s (guards in `e2e/share-removed.spec.ts`, `api/tests/test_share_removed_122.py`). |
| Scope picker removed (QA-BF1) | No data-source/scope control (guard in `e2e/backend-fit.spec.ts`). |
| Dead code / TODOs | No `ShareDialog`/`SharedView`/`ScopePopover`/`readOnly`/`sqlWithheld` code left (removed in #302/#305/#312). No TODOs point at removed features. |

**Open deviations** (none P1 or P2; each `gap()` is a ratchet that turns red when the fix lands, so remove it then):
- P3, last recorded open and not re-verified at finalize:
  - QA-S13/S24: browser Ctrl+F in off-screen turns of a long virtualized thread, unverified on Firefox/WebKit.
  - QA-S17: theme radios are one Tab stop each.
  - QA-S32: jump-to-latest is close to the composer.
  - QA-A16: the thinking header duration can be shorter than its longest step.
  - QA-A17: the jump button can overlap content.
  - QA-A28: code Copy is icon-only (optional).
- BACKEND-FIT questionable items for the PM (not bugs; see §8 and the sprint notes `/workspace/cc_fe/NOTES.md`, "BACKEND-FIT"):
  - BF3: "+" attaches text as context only; the analyzer can't query it.
  - BF4: the strategy/model picker is validated and echoed but not wired to the scripted run.
  - BF5/BF6: report versions and multi-question clarify are demo-only until the analyzer emits them.

**How to re-run:**
- Run `cd web && npm run ci`.
- Then run `npm run e2e`, `npm run e2e:chaos` and `npm run e2e:xb` (WebKit needs `npx playwright install-deps webkit`).
- Optionally run `npm run e2e:perf`.
- All use one demo server and `--workers=1`. After an intended UI change, refresh the baselines with `npm run e2e:update` and review the PNG diffs before committing.
- Review scripts (side-by-side, slop audit, keyboard/SR walks) are in `web/e2e/review/`.
- The findings docs are `docs/frontend-fidelity-audit.md`, `docs/frontend-fidelity-behaviour.md`, `docs/frontend-side-by-side.md` and `docs/frontend-parity-checklist.md`.
