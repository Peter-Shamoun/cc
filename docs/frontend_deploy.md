# Production deploy checklist (web/ + api/)

This lists everything the demo can't enforce for itself. Each item needs an owner and a tick before real PHI goes
through the app. The demo (`ScriptedLLM`, `DemoAuth`, synthetic data) needs none of it.

`api/tests/test_deploy_doc.py` fails if a `CC_*` environment variable the API reads isn't listed here, or if a
section below is removed.

## 1. Sign-out revokes the session server-side (owner: Peter / backend)

`POST /api/auth/sign-out` already wipes the browser: `Clear-Site-Data: "cache", "storage"` plus expired `cc_session`
cookies (see [Sign-out](frontend_security.md#sign-out)). It does not end the session at the identity provider. Nobody
has implemented that part yet.

- [ ] The production `AuthBackend.sign_out(token)` calls **Cognito `GlobalSignOut`**, or `AdminUserGlobalSignOut`
      with the user's sub. That invalidates the refresh token, so a copied `cc_session` cookie or token stops
      working everywhere.
- [ ] If sessions live in a server-side table, the same method deletes the row.
- [ ] It's idempotent: the route calls it with whatever token arrived, even an expired one.
- [ ] It must never raise for an unknown or expired token. The route catches errors and logs only the exception
      type, but a raise skips the revoke silently.
- [ ] It never logs the token.
- [ ] Access tokens stay valid until they expire, even after GlobalSignOut. Keep their lifetime short (Cognito
      default 60 min; recommend 15 min or less) and re-validate them per request.
- [ ] Test: sign in, copy the cookie, sign out, replay the cookie, and expect **401** within the access-token
      lifetime. The demo can't run this test: `DemoAuth` and `SimAuth` tokens are stateless.
- [ ] A shell "Sign out" menu item calls `signOut()` from `web/src/auth/session.ts` (not wired in demo mode).

## 2. Nothing rewrites `/assets/` (owner: Peter / infra)

Built HTML pins every script and stylesheet with **Subresource Integrity**, and an import map pins every lazy chunk
(see [Subresource Integrity](frontend_security.md#subresource-integrity)). If any byte of a served file changes after
`vite build`, the browser refuses it and the app doesn't boot.

- [ ] CDN, WAF and proxy features that rewrite JS, CSS or HTML are **off** for this origin. Examples:
      auto-minify, Rocket Loader / script deferral, "mirage"/image rewriting, ad or analytics injection,
      email obfuscation, and HTML "optimisation".
- [ ] gzip and brotli are fine: integrity is checked on the decoded body.
- [ ] `/assets/*` is deployed **atomically with `index.html`**. Old hashed files are kept for at least one cache
      lifetime so open tabs can still lazy-load. Don't overwrite a hashed filename in place.
- [ ] `/assets/*` is served as `Cache-Control: public, max-age=31536000, immutable`. Everything else, including
      `index.html` and every SPA route, is `no-store`.
- [ ] After each deploy, run `node web/scripts/sri_check.mjs <deployed dist>` and do a smoke load in Chrome with the
      console open. A line like "Failed to find a valid digest" means something is rewriting assets.

## 3. HTML is served with a fresh CSP nonce

- [ ] The server replaces `__CC_CSP_NONCE__` in `index.html` with a fresh random nonce (at least 128 bits) for
      **every HTML response**, and sends the matching CSP header. Never serve `dist/index.html` raw, and never use
      a fixed nonce. `vite preview`'s `securityHeadersPlugin` is the reference.
- [ ] The full header set from [frontend_security.md](frontend_security.md) is on every response, including
      404/5xx and SPA fallbacks. That covers CSP with Trusted Types, Referrer-Policy `no-referrer`, nosniff, XFO
      DENY, Permissions-Policy and COOP/CORP. Add HSTS at the edge.
- [ ] Check with `node web/scripts/header_check.mjs https://<host>/` and
      `CSP_MODE=live node web/scripts/csp_check.mjs https://<host>/`.

## 4. Auth and session

- [ ] Set `CC_API_AUTH` to the production mode and pass an explicit `auth=` backend. `create_app()` refuses to
      start otherwise.
- [ ] The `cc_session` cookie is `HttpOnly; Secure; SameSite=Strict; Path=/api`.
- [ ] Build the web app with `VITE_AUTH=cookie` and `VITE_SIGN_IN_URL`, and **without**
      `VITE_ANALYST_DEMO_TOKEN`.
- [ ] Every cookie-authenticated write sends `x-cc-csrf: 1`. The API already enforces this; don't add CORS.
- [ ] The sign-in URL's `next=` gets only the path. It can carry a share id (`/shared/sh_…`, share review S3), so the
      IdP must not log or forward `next`.

## 5. Logs and PHI

- [ ] Run the API through `create_app()` so its log redaction is installed: route-shaped access-log paths
      (`/api/shared/:id`) and IPs truncated to /24 (IPv4) or /48 (IPv6). Under gunicorn or another server, check
      that its access logger is `uvicorn.access`, or add `AccessLogFilter` to it.
- [ ] **Edge and load-balancer access logs** (ALB, CloudFront, nginx) log the full request line, which includes
      share ids and conversation ids. Either log the route shape, drop the path, or treat those logs as PHI-bearing
      (encrypted, access-controlled, with retention ≤ the transcript retention).
- [ ] Error trackers and APM (Sentry, Datadog…) are off, or set to scrub URLs, request bodies and breadcrumbs. The
      web app sends nothing on its own (`connect-src 'self'`).

## 6. Models and paid APIs

- [ ] `CC_API_LLM` stays `scripted` until the BAA and model decision are signed off.
- [ ] `CC_API_ALLOW_PAID_API` is unset everywhere except the one production environment that's approved for it.

## 7. Shares (pending a contract decision)

These are tracked from [the share security review](frontend_share_security_review.md) and are now with the Contract
team:
- [ ] **S1**: widening a share to include data issues a **new link**, so the old link keeps its narrower scope.
- [ ] **S4**: shares expire. The default is **30 days**, with 7, 30 and 90 day options shown in the share dialog.
- [ ] The share registry is persistent, scoped to the same customer key as transcripts, and revoked when the
      conversation is deleted.

## 8. Environment variables the API reads

| Variable | Default | Production |
|---|---|---|
| `CC_API_AUTH` | `demo` | the real auth mode (explicit backend required) |
| `CC_API_MODE` | demo | not `sim` (the ccsim world) |
| `CC_API_LLM` | `scripted` | `scripted` until sign-off (§6) |
| `CC_API_ALLOW_PAID_API` | unset | unset unless approved (§6) |
| `CC_API_SQL_LITERAL_MASKING` | `phi` | `phi` or `all`; never `off` (shown SQL would carry PHI literals; contract 1.14.0 §10) |
| `CC_API_HOST` / `CC_API_PORT` | `127.0.0.1` / `8787` | behind the proxy |
| `CC_API_MAX_BODY_BYTES` | 1 MiB | keep; set the proxy's body limit ≥ this so oversize requests get the API's 413 `payload_too_large` |
| `CC_API_MAX_CONCURRENT_RUNS` | `3` unfinished runs per user | keep, or size to capacity |
| `CC_API_HEARTBEAT_SECONDS` | `15` | must be below every proxy idle timeout (ALB default 60 s) |
| `CC_API_DISCONNECT_GRACE_SECONDS` | `10` | keep |
| `CC_API_DEMO_PACE`, `CC_API_STREAM_DELAY_SECONDS`, `CC_API_MODEL_DELAY_SECONDS` | demo pacing | `0` / unset |
| `CC_API_ORG_POLICY` | unset | per-customer model and strategy policy (JSON) |
| `CC_TRANSCRIPT_MODE` | `ephemeral` | the decided retention mode ([frontend_transcripts.md](frontend_transcripts.md)) |
| `CC_TRANSCRIPT_DIR`, `CC_TRANSCRIPT_KEYS_DIR` | unset | encrypted volume; keys from KMS, not on the same disk |
| `CC_TRANSCRIPT_SWEEP_SECONDS` | `3600` | keep |

Proxies must not buffer `text/event-stream`. For nginx, that means `proxy_buffering off` or the
`X-Accel-Buffering: no` header the API sends; otherwise answers arrive all at once.
