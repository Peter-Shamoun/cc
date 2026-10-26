# Frontend security headers

Owner: platform team (frontend SOTA sprint). Source of truth:
`web/src/security/headers.ts` (web) and `api/security.py` (API). Keep the Permissions-Policy lists identical;
`api/tests/test_headers.py::test_permissions_policy_matches_web` fails if they drift.

## Web app (Vite dev, Vite preview, production)

| Header | Value |
|---|---|
| Content-Security-Policy | `default-src 'self'; script-src 'self' 'nonce-…'; style-src 'self' 'nonce-…'; style-src-elem 'self' 'nonce-…'; style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; media-src 'self' blob:; manifest-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-src 'none'; frame-ancestors 'none'; upgrade-insecure-requests` |
| Cache-Control | `no-store` on HTML (and everything outside `/assets/`); hashed `/assets/*` are `public, max-age=31536000, immutable` |
| Referrer-Policy | `no-referrer` |
| X-Content-Type-Options | `nosniff` |
| X-Frame-Options | `DENY` (legacy twin of `frame-ancestors 'none'`) |
| Permissions-Policy | every powerful feature off; `clipboard-write=(self)` (copy buttons), `fullscreen=(self)` |
| Cross-Origin-Opener-Policy / -Resource-Policy | `same-origin` |

Why each relaxation exists:

* **No `'unsafe-inline'` / `'unsafe-eval'` for scripts.** The pre-paint theme script moved to
  `web/public/theme-init.js`. Vega used `new Function` for expressions; `ChartView` now passes `ast: true`
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
* `web/src/security/headers.test.ts` — policy shape, no inline script/style in `index.html`, no
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
  - `<ErrorBoundary scope="section" name="…">` is an inline "This part couldn't be displayed · Try again" card. Teams can wrap risky subtrees with it (artifact panel, charts) so one bad part doesn't take down the page.
  - `web/src/components/ErrorBoundary.test.tsx` throws synthetic PHI (name, MRN, SSN, DOB, email) and asserts none of it reaches the screen or the clipboard.
- **Residual:** React 18 still `console.error`s errors that a boundary catches, even in production. React 19's `createRoot({ onCaughtError })` closes this gap; do it on upgrade.

**API.**
- `api/redact.py` mirrors the client rules (shared vectors: web/src/lib/redact.vectors.json).
- `install_log_redaction()` runs in `create_app`. It attaches `PhiLogFilter` to root, cc.api, uvicorn*, fastapi and starlette loggers, and their handlers. The filter redacts every message and replaces tracebacks with `Type (message redacted)` plus `file:line in func` frames, with no source text.
- `ErrorShieldMiddleware` (inside SecurityHeaders) turns any unhandled exception into the contract's 500 envelope, `{"error":{"code":"internal",...}}`. It logs only `Type at file:line` and the route template, and doesn't re-raise, so uvicorn never prints the message.
- 422s use the contract envelope with field paths only (`Invalid request: messages.0.id.`). User-controlled keys are masked as `*`. FastAPI's default 422 echoed the offending `input` back.

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
