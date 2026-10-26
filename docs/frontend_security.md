# Frontend security headers

Owner: platform team (frontend SOTA sprint). Source of truth:
`web/src/security/headers.ts` (web) and `api/security.py` (API). Keep the Permissions-Policy lists identical;
`api/tests/test_headers.py::test_permissions_policy_matches_web` fails if they drift.

## Web app (Vite dev, Vite preview, production)

| Header | Value |
|---|---|
| Content-Security-Policy | `default-src 'self'; script-src 'self' 'nonce-…' 'wasm-unsafe-eval'; style-src 'self' 'nonce-…'; style-src-elem 'self' 'nonce-…'; style-src-attr 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self' blob:; media-src 'self' blob:; manifest-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-src 'none'; frame-ancestors 'none'; upgrade-insecure-requests` |
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
* **`'wasm-unsafe-eval'`** lets Shiki's regex engine compile WebAssembly. It does not allow JS eval.
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
