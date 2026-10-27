### Amazon CloudFront
| | |
|---|---|
| Purpose in ClaimClarity | The one public entry point. It serves the web app and passes `/api/*` to the chat API on the same origin, so there is no CORS and the session cookie can be SameSite=Strict. |
| Talks to | Browsers (HTTPS). The web app bucket through origin access control (`/assets/*`). A Lambda@Edge function that returns `index.html` with a fresh CSP nonce. The Application Load Balancer (`/api/*`). AWS WAF runs on the distribution. |
| Permissions | Origin access control may read the web app bucket only. The Lambda@Edge role may read the `index.html` template and write its own logs. The deploy role may create invalidations. Nobody else changes the distribution, except the infrastructure pipeline. |
| Encryption | TLS 1.2 or higher with an ACM certificate to browsers, and HTTPS to the ALB. The web app bucket uses SSE-S3. |
| Data held / PHI | Caches only hashed `/assets/*` (no PHI). HTML and every `/api/*` response are `no-store` and never cached. API responses, which can hold PHI, pass through without being stored. |
| Scaling and limits | Managed. Origin read timeout 60 s, which is above the 15 s SSE heartbeat. `/assets/*` cached for one year (immutable). Edge features that rewrite JS, CSS or HTML stay off, because Subresource Integrity would reject the files. |
| Failure behaviour | An unreachable origin returns 5xx. The web app retries API calls and reconnects SSE streams. A Lambda@Edge failure stops the app from loading, but does not affect the API. |
| Logging and audit | Standard access logs go to the access log bucket (separate from the ClaimClarity bucket). Paths carry opaque ids, so the logs are treated as PHI-bearing: encrypted, security team only, kept 1 year. Configuration changes are recorded in CloudTrail. |
| Used on pages | [Edge](../pages/31-edge.md), [Deploy](../pages/61-deploy.md), [Chat backend](../pages/30-chat-backend.md) |
