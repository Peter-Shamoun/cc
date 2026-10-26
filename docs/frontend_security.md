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

API CSV downloads use the client's name, `claimclarity_<slug>_<yyyy-mm-dd>.csv` (UTC date). The slug comes from the part
title, never from row values or question text. `api/store.py csv_filename` and `web/src/lib/csv.ts csvFilename` share the
test vectors in `web/src/lib/csvFilename.vectors.json`. The Content-Disposition value goes through `_safe_filename`
([A-Za-z0-9._-] only), so a name can't inject a header or a path.
