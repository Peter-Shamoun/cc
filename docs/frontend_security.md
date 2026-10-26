# Frontend security headers

Owner: platform team (frontend SOTA sprint). Source of truth:
`web/src/security/headers.ts` (web) and `api/security.py` (API). Keep the Permissions-Policy lists identical;
`api/tests/test_headers.py::test_permissions_policy_matches_web` fails if they drift.

## Web app (Vite dev, Vite preview, production)

| Header | Value |
|---|---|
| Content-Security-Policy | `default-src 'self'; script-src 'self' 'nonce-…'; style-src 'self' 'nonce-…'; style-src-elem 'self' 'nonce-…'; style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; media-src 'self' blob:; manifest-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-src 'none'; frame-ancestors 'none'; require-trusted-types-for 'script'; trusted-types cc-html default; upgrade-insecure-requests` (dev adds `'allow-duplicates'` for HMR) |
| Cache-Control | `no-store` on HTML (and everything outside `/assets/`); hashed `/assets/*` are `public, max-age=31536000, immutable` |
| Referrer-Policy | `no-referrer` |
| X-Content-Type-Options | `nosniff` |
| X-Frame-Options | `DENY` (legacy twin of `frame-ancestors 'none'`) |
| Permissions-Policy | every powerful feature off; `clipboard-write=(self)` (copy buttons), `fullscreen=(self)` |
| Cross-Origin-Opener-Policy / -Resource-Policy | `same-origin` |

Why each relaxation exists:

* **No `'unsafe-inline'` / `'unsafe-eval'` for scripts.** The pre-paint theme script lives in
  `web/public/theme-init.js` (single source); the build inlines it into `index.html` as a nonce'd script
  (`web/scripts/boot_shell.mjs`, QA-P8: one fewer render-blocking round trip). It reads only allowlisted UI prefs
  and records whether a chat is open, never the id. The static boot shell's `<style>` is nonce'd the same way;
  `checkHtmlNonce` fails the header/CSP gates if any inline element lacks the response nonce. Vega used `new Function` for expressions; `ChartView` now passes `ast: true`
  so vega-embed uses `vega-interpreter`.
* **No `'wasm-unsafe-eval'`.** Shiki runs its JavaScript regex engine (fine-grained imports in CodeBlock.tsx), so no WebAssembly is compiled.
* **`style-src-attr 'unsafe-inline'`** only. Shiki token colours, KaTeX layout and Vega SVG set inline
  `style` attributes; these cannot be hashed. `<style>` elements still need `'self'` or the nonce.
  vega-embed's and vega-tooltip's injected stylesheets are disabled (`defaultStyle: false`,
  `tooltip.disableDefaultStyle`) and themed in `web/src/index.css` instead.

### Nonce
`vite build` writes the placeholder `__CC_CSP_NONCE__` into every `nonce=""` attribute it emits
(`html.cspNonce`), plus `<meta property="csp-nonce">` that Vite's runtime reads for injected CSS.
**A production server must replace the placeholder with a fresh random nonce per HTML response and send
the matching CSP header** (as `vite preview` does in `securityHeadersPlugin`, `web/vite.config.ts`). Never
serve `dist/index.html` raw with a static nonce. Dev uses one nonce per server start and adds `ws:` to
`connect-src` for HMR.

## API (`api/security.py`)
Every response (JSON, SSE, CSV, 401/404/422, unknown routes) gets `Cache-Control: no-store`,
`Pragma: no-cache`, a deny-all CSP (`default-src 'none'; frame-ancestors 'none'; base-uri 'none';
form-action 'none'; sandbox`), and the same Referrer/nosniff/XFO/Permissions/COOP/CORP headers.
It is a pure ASGI middleware, so SSE is not buffered. Route-level values for the same names are replaced,
so there is exactly one `Cache-Control`.

## Checks
* `pytest api/tests/test_headers.py` — headers on every response type.
* `web/src/security/headers.test.ts` — policy shape, `index.html` source has no inline script and only the
  boot-shell `<style>`, the boot-shell transform passes `checkHtmlNonce`, no
  `eval`/`new Function`/`createElement('style')` in `src/`, Vega interpreter options present.
* `npm run check:csp -- <url> [shotDir]` (Playwright, `web/scripts/csp_check.mjs`) — loads a running build,
  asks the live demo questions (or every fixture with `CSP_MODE=mock`), and fails on any CSP violation,
  console error, missing header, or a rendered row value found in localStorage/sessionStorage/URL/console.
  Verified clean on 2026-10-25 against `vite preview` (live demo API and mock) and `vite` dev.

## CSV / TSV formula injection

Every spreadsheet export path, server and client, defuses formula-looking text: the API download (`api/store.py
defuse_cell`), and Download CSV, Copy table (TSV) and chart "Download data" (`web/src/lib/csv.ts defuse`). A text cell
or header that starts with `= + - @`, TAB or CR gets a leading `'`, so Excel or Sheets shows it as text instead of running
it (e.g. `=HYPERLINK(...)` exfiltration). Numbers, negatives included, stay numeric. Tests: `api/tests/test_csv_injection.py`,
`web/src/security/csvInjection.test.ts`.

## Request-body cap

`api/security.py BodyLimitMiddleware` (pure ASGI, inside the security-headers middleware) rejects POST/PUT/PATCH bodies
over 1 MiB (`CC_API_MAX_BODY_BYTES`) with 413 `{"error":{"code":"validation",…}}` before the route parses them. A declared
Content-Length over the limit is refused unread, and chunked bodies are counted. GET (incl. SSE) is untouched. A
60-message history at about 7 KB each (420 KB) still fits. Tests: `api/tests/test_body_limit.py`.

## Concurrent-run cap

`POST /api/chat` allows at most 3 unfinished runs per user (`CC_API_MAX_CONCURRENT_RUNS`). Beyond that it returns 429
`rate_limited` with `Retry-After: 3`, which the web client already auto-retries. Nothing is stored or run, and the audit
event `chat_rate_limited` (reason `concurrent_runs`) carries no question text. Other users are unaffected. Tests:
`api/tests/test_concurrent_runs.py`.

## Download filenames

API CSV downloads use the client's name, `cc_<slug>_<yyyy-mm-dd>.csv` (UTC date). The slug comes from the part
title, never from row values or question text. `api/store.py csv_filename` and `web/src/lib/csv.ts csvFilename` share the
test vectors in `web/src/lib/csvFilename.vectors.json`. The Content-Disposition value goes through `_safe_filename`
([A-Za-z0-9._-] only), so a name can't inject a header or a path.

## PHI-safe error reporting and logs

**Client.**
- `web/src/lib/errorReporting.ts` is the only error sink.
  - `reportError(e, kind, context)` reduces anything thrown to `{name, message, frames, id, at, kind, context}` via `safeError()` and `redact()` (web/src/lib/redact.ts).
  - Frames are `file:line:col` only. Context is allowlisted (`component`, `action`, `code`, `status`). Thrown non-Errors are never stringified.
  - Records live in a 20-entry in-memory ring. There's no storage, URL, console or network use.
  - The later backend plugs in with `setReporter(fn)`, which only ever sees redacted records.
- `installGlobalErrorHandlers()` (in main.tsx) captures `error` and `unhandledrejection`. In production builds it calls `preventDefault()`, so the browser doesn't print the raw error to the console.
- `ErrorBoundary` wraps `<App/>`. Its **crash screen** shows a generic message, never the error text, plus:
  - **Reload** (autofocused).
  - **Start a new chat**, which drops the `#/c/<id>` route so a conversation that crashes on load can't trap the user in a reload loop.
  - A collapsed **"diagnostics for support"** block. The block shows exactly what **Copy diagnostics** puts on the clipboard (`web/src/lib/diagnostics.ts`):
    - Allowed: reference id, ISO time, contract version and build mode, error class and kind, bundle frames, allowlisted context, route *kind*, browser family/major/OS, viewport, theme, online flag, and other recent error ids.
    - Never included: the message (even redacted), conversation ids, the raw user agent, titles, or text.
  - When a lazy chunk fails to load (dropped connection or a new deploy), the copy says so and the record carries `code: chunk_load`.
  - `<ErrorBoundary scope="section" name="…">` is an inline "This part couldn't be displayed · Try again" card, so one bad part doesn't take down the page. Optional props: `onDismiss`/`dismissLabel` (a second button), `fallbackClassName`, and `resetKey` (it retries by itself when this changes).
  - Wrapped at the definition, so every call site (chat, shared view, panel) gets the boundary with no call-site edits:
    - `ChartView` and `DataTable`: an inline card in place of the part. They retry when the part changes, e.g. as the stream fills it in.
    - `ArtifactPanel`: a panel-shaped card with "Try again" and "Close panel". It retries when another artifact or version opens, and the chat stays usable.
  - Tests: `src/components/sectionBoundaries.test.tsx`.
  - `web/src/components/ErrorBoundary.test.tsx` throws synthetic PHI (name, MRN, SSN, DOB, email) and asserts none of it reaches the screen or the clipboard.
- **Residual:** React 18 still `console.error`s errors that a boundary catches, even in production. React 19's `createRoot({ onCaughtError })` closes this gap; do it on upgrade.

**API.**
- `api/redact.py` mirrors the client rules (shared vectors: web/src/lib/redact.vectors.json).
- `install_log_redaction()` runs in `create_app`. It attaches `PhiLogFilter` to root, cc.api, uvicorn*, fastapi and starlette loggers, and their handlers. The filter redacts every message and replaces tracebacks with `Type (message redacted)` plus `file:line in func` frames, with no source text.
- **Access logs** (`AccessLogFilter`, installed by `create_app` on `uvicorn.access`) log the route *shape*, never the path. A segment is kept only if it's a static segment of a registered route; anything else (share ids, conversation, message and file ids, probe junk) becomes `:id`. A query string becomes `?…`, and client IPs (a HIPAA identifier) are truncated to /24 or /48. Example: `203.0.113.0 - "GET /api/shared/:id HTTP/1.1" 200`. This holds for the plain `uvicorn api.app:app` command (access log on), as used by Playwright and the docs. `python -m api` still turns the access log off. `api/tests/test_access_log.py` starts a real uvicorn and checks its output.
- `ErrorShieldMiddleware` (inside SecurityHeaders) turns any unhandled exception into the contract's 500 envelope, `{"error":{"code":"internal",...}}`. It logs only `Type at file:line` and the route template, and doesn't re-raise, so uvicorn never prints the message.
- `HeadMiddleware` (innermost) answers `HEAD` on every `GET` route: it runs the GET, keeps its status and headers (including `content-length` and the security headers), and drops the body. Starlette routes declared with `@app.get` answered `HEAD` with 405, which broke uptime checks, `curl -I` and link previews. `HEAD .../stream` stays 405: a HEAD there would attach to a live run and look like a viewer that dropped. Tests: `api/tests/test_head_and_outbound_log.py`.
- 422s use the contract envelope with field paths only (`Invalid request: messages.0.id.`). User-controlled keys are masked as `*`. FastAPI's default 422 echoed the offending `input` back.

- **Outbound HTTP logs** (`OutboundUrlFilter`, installed by `create_app` via `install_outbound_url_redaction()` on the `httpx` and `httpcore` loggers). httpx logs every request at INFO as `HTTP Request: GET http://testserver/api/shared/sh_… "HTTP/1.1 200 OK"`, i.e. full URLs with share and conversation ids. The test client and any future backend client go through it. The filter keeps scheme and host, reduces the path to its route shape, and drops query and fragment.

**Heuristics, not de-identification.** Quoted strings, emails, SSNs, phones, dates, 6+ digit numbers and 8+ char alphanumeric ids are masked. A bare unquoted name is not. The primary rule still stands: never pass user text, SQL or row values to a logger or `reportError`.

## Session / auth boundary (for the real backend)

**One seam per side.**
- Client: `web/src/auth/session.ts`.
  - `AuthSession` has `{kind, headers(), onExpired()}`.
  - `sessionFromEnv()` picks:
    - `cookieSession` when `VITE_AUTH=cookie` (optional `VITE_SIGN_IN_URL`)
    - `demoTokenSession` when `VITE_ANALYST_DEMO_TOKEN` is set
    - `noSession` otherwise (the offline mock)
  - Both HTTP adapters take the session and call `onExpired()` on 401. Nothing else in the UI knows how auth works.
- API: `api.backends.AuthBackend` (`mode`, `authenticate(token, request_id) -> AuthContext`), passed as `create_app(auth=...)`.

**Rules the real backend inherits (tested in `api/tests/test_auth_boundary.py` and `web/src/auth/session.test.ts`):**
- The session cookie is `cc_session`: `HttpOnly; Secure; SameSite=Strict; Path=/api`, set by the backend's sign-in. JS never reads it.
- Cookie-authenticated `POST/PUT/PATCH/DELETE /api/*` must send `x-cc-csrf: 1`. Without it the API answers 403 `access_denied` (`CsrfMiddleware`). The API grants no CORS, so a cross-site page can't add the header. Bearer (demo) requests aren't cookie-borne and skip the check.
- Credentials are read only from `Authorization` or the cookie, never from a query parameter.
- With `CC_API_AUTH` set to anything but `demo`, `create_app()` refuses to start without an explicit `auth=` backend. There's no silent fallback to demo tokens.
- A token baked into the bundle is public. `vite build` and `sessionFromEnv()` refuse a `VITE_ANALYST_DEMO_TOKEN` that isn't the synthetic `demo:<customer>:<user>` shape.
- `onExpired` for cookie sessions sends the user to `VITE_SIGN_IN_URL?next=<pathname>`, once, with no query or hash.

### Sign-out

**`POST /api/auth/sign-out` → 204** (`api/app.py`, tested in `api/tests/test_sign_out.py`). The real backend inherits it unchanged.
- **Server side:** the route calls `AuthBackend.sign_out(token)` with whatever token the request carried, even an expired one. The real backend revokes the session there (Cognito `GlobalSignOut` or deleting the session row), so a copied cookie stops working. `DemoAuth` and `SimAuth` make it a no-op, since their tokens are stateless. A backend without the method, or one that raises, still gets the browser wipe. Only the exception type is logged, never the token.
- **Browser side, always:**
  - `Clear-Site-Data: "cache", "storage"` wipes this origin's HTTP cache, localStorage (UI prefs), sessionStorage, IndexedDB, Cache Storage and any service worker. That's the last line of defence on a shared billing-office PC.
  - Two expired `cc_session` cookies, `Path=/api` (the spec'd path) and `Path=/`, both `HttpOnly; Secure; SameSite=Strict; Max-Age=0`. A cookie is only removed by a matching `Path`.
  - The route doesn't send `"cookies"` in Clear-Site-Data, because that clears every cookie on the registrable domain, signing users out of sibling apps.
- **CSRF:** a cookie-borne sign-out needs `x-cc-csrf: 1` like every write, so a third-party page can't force a sign-out (403, no wipe).
- **Audit:** `signed_out` is recorded with customer and user only, and only when the token was still valid.
- **Client:** `signOut(base, session, {signInUrl})` in `web/src/auth/session.ts` POSTs and then navigates to sign-in, or reloads. The navigation also drops in-memory drafts and answers. It navigates away even when the request fails (offline), because the user asked to leave. No UI calls it yet: shell should wire a "Sign out" item once the real backend exists, since demo mode has no sign-in to return to.
- **Other tabs:** `Clear-Site-Data` empties storage, but another open tab keeps its conversation on screen (in memory) until its next request. `signOut` therefore posts the constant `signed-out` on the `cc-auth` BroadcastChannel (no ids or data). Every cookie session listens (one listener per page) and leaves for sign-in with `next=<path>`, or reloads, which drops what's on screen. The signing-out tab ignores its own echo. Tests: `web/src/auth/crossTabSignOut.test.ts`.

## Caching and offline

**Decision: no service worker and no offline shell.** The app ships no worker script, manifest, Cache Storage or IndexedDB. `web/src/security/noServiceWorker.test.ts` enforces this.

Why:
- **PHI stays off disk.** A service worker caches responses in Cache Storage. That puts answers, tables, CSVs, titles and shared views on disk, where they outlive sign-out, retention (`PUT /api/transcripts/retention`), share revocation and customer-key shredding. There is no reliable way to purge copies on a machine that never comes back online. Spec §6.2 allows only allowlisted UI prefs in browser storage.
- **Shared workstations.** Billing offices share PCs. With `no-store`, the next user of the browser profile finds nothing in its caches.
- **Blast radius.** A worker is a long-lived script that intercepts every request on the origin, so an XSS that registers one persists after the tab closes. With no worker, an XSS ends with the page. `worker-src` stays limited to `'self' blob:`, which Vega and Shiki need for dedicated workers.
- **Little to gain.** Every answer needs the server. Offline, the composer keeps its in-memory draft and shows the offline notice; sending waits for reconnect.

What covers fast loads instead:
- the static boot shell in `index.html`, which paints from the HTML alone (QA-P8, `docs/frontend_a11y_perf.md`)
- the lazy chunks
- the browser HTTP cache for content-hashed assets (code, no PHI; `immutable` is allowed by `header_rules.mjs`)

When to revisit: only if a real offline requirement appears. Even then, cache hashed static assets only, never `/api/` or HTML, and rely on the sign-out route's `Clear-Site-Data: "cache", "storage"` (see "Sign-out") to purge it.

### Cache-control audit of every API route

`api/tests/test_cache_audit.py` enumerates `app.routes`, so a new route fails the test until it's added to the table. Every route is exercised on its success path and at least one error path: 401, 404 or 422. Signed-out `health`, `contract` and `capabilities` return generic data with no user data. Framework-level 404/405, OPTIONS and HEAD are covered too. Every response must carry:
- `Cache-Control: no-store` exactly (no `public`, `max-age` or `immutable`)
- `Pragma: no-cache`
- the full `SECURITY_HEADERS` set

No response may carry `ETag`, `Last-Modified`, `Expires` or `Age`, and forged `If-None-Match`/`If-Modified-Since` never produce a 304.

| Route | Data | Result |
|---|---|---|
| `GET /api/health`, `/api/contract`, `/api/capabilities` | none / schema / picker options | no-store ✓ |
| `POST /api/chat`, `GET /api/chat/{id}/stream`, `POST /api/chat/{id}/stop` | answers (SSE) | no-store ✓, `X-Accel-Buffering: no` |
| `GET /api/download/{id}` | CSV (PHI) | no-store ✓, attachment |
| `GET /api/transcripts`, `PUT /api/transcripts/retention` | account state | no-store ✓ |
| `GET`/`DELETE /api/conversations`, `POST /api/conversations/search` | titles, search (POST so text never sits in a URL) | no-store ✓ |
| `GET`/`PATCH`/`DELETE /api/conversations/{id}`, `GET …/export` | transcripts (PHI) | no-store ✓ |
| `PUT`/`DELETE …/messages/{mid}/feedback` | feedback | no-store ✓ |
| `GET`/`PUT`/`DELETE …/share`, `GET /api/shared/{id}` | share link, shared view | no-store ✓, shared view `no-referrer` |

The audit found no gaps: `SecurityHeadersMiddleware` overrides any per-route value on every status. The web side is checked separately by `header_rules.mjs`: HTML is no-store, and hashed assets are no-store or immutable.

## Trusted Types

The web CSP enforces Trusted Types: `require-trusted-types-for 'script'; trusted-types cc-html default`. A plain string reaching a DOM-XSS sink now **throws** instead of executing. That covers `innerHTML`/`outerHTML`/`insertAdjacentHTML`, `document.write`, `<script>` src/text and `eval`-like calls, whether in our code or any dependency. A DOM-XSS bug becomes a broken render, not script execution. The code is in `web/src/security/trustedTypes.ts`.

**Policies:**
- **`cc-html`** has exactly two producers:
  - `shikiHtml()`, Shiki's highlighted code (CodeBlock's `dangerouslySetInnerHTML`). It only accepts markup starting with `<pre` and containing no script/iframe/object/embed/link/meta/base/form/style/img/svg tags, `javascript:` or quoted `on*=` handlers. Anything else falls back to plain `<pre><code>` text.
  - `tooltipHtml()`, chart tooltips (vega-tooltip's `formatTooltip`). It produces the same `<h2>` + key/value `<table>` markup as vega-tooltip's default, with our own escaping of `& < > " '`. It **drops the `image` field**, so a model-authored spec can't make the tooltip load a URL.
- **`default`**, the browser's fallback for plain strings, accepts only `''`: vega-embed clears its container with `innerHTML = ''` on every render. Any other string is rejected.
- `installTrustedTypes()` runs in main.tsx before the first render.

**Why React works:** React 18 passes `dangerouslySetInnerHTML.__html` straight to `innerHTML`, so a TrustedHTML object goes through. Nothing else in the app or its rendering dependencies (react-markdown, KaTeX, Vega/vega-lite on the AST interpreter, Shiki) uses a sink.

**Verification:**
- `scripts/csp_check.mjs` replays every mock fixture under the enforcing CSP with no violations.
- Manual run in headless Chrome: code is highlighted (73 Shiki spans), the chart tooltip renders, and a deliberate `innerHTML = '<b>x</b>'` is blocked. Shots are in shots/platform/trusted-types/.
- `scripts/header_rules.mjs` fails the header check if `require-trusted-types-for 'script'` is missing, or if `trusted-types` is `*` or allows duplicates.

**Adding a sink:** don't. Render with React elements. If a library needs HTML, add a narrowly-validated producer to `trustedTypes.ts` with a test, and say why here. Never widen `default` and never add `'allow-duplicates'` or `*` in production. Browsers without Trusted Types (Firefox, older Safari) ignore the directive; React's escaping still applies there.

## Subresource Integrity

Production builds (`vite build`, including the mock build) pin every built script and stylesheet by hash. This is done by `sriPlugin` in `web/vite.config.ts`, which calls `web/scripts/sri.mjs`.

**What's covered:**
- **Static tags.** The entry `<script type="module">`, every `<link rel="modulepreload">` and the stylesheet get `integrity="sha384-…"` (plus `crossorigin`, which Vite already adds). The browser refuses a file whose bytes don't match.
- **Lazy chunks.** These include vega, Shiki grammars and the markdown renderer, loaded with `import()`, which has no integrity attribute. They're covered by an **import map with an `integrity` section** placed before the first module script. It carries the CSP nonce like every other inline script, and it lists every `.js` file in `assets/`.
  - Chrome 127+ and Safari 18+ enforce it. Other browsers ignore the key and still load the files.

**How it's applied:** the hashes are computed in `writeBundle`, after the files are on disk, so they cover the exact served bytes.

**Requirements for serving:**
- A server or CDN may compress `/assets/` files: integrity is checked on the decoded body.
- It must **not** rewrite them (minify, inject, or "optimise"), or the app won't boot.
- Dev (`vite`) has no SRI because files change on every edit.

**Verification:**
- `web/src/security/sri.test.ts` (7 tests): hashing; placement before module scripts and modulepreloads; idempotence; that the check catches a tampered file, a missing attribute, a missing crossorigin, a late map or an unlisted chunk; that the build fails if a referenced asset is missing; and `<` escaping in the map.
- **Local gate:** `node scripts/sri_check.mjs dist dist-mock` re-hashes both builds.
- Then `csp_check.mjs` loads the SRI'd mock build and fails on any console error, which would include an integrity failure.
- **Manual check in Chrome 156 (2026-10-25, 21:58 PDT):** a clean build loads a lazy chunk with no console errors. After one byte was appended to that chunk on disk, `import()` was rejected with "Failed to find a valid digest in the 'integrity' attribute".

**Limits:**
- KaTeX fonts are loaded from CSS, and fetched fonts have no SRI.
- Integrity doesn't protect against a compromised build, only against tampering after the build: on a CDN, in a cache, or by a proxy.

