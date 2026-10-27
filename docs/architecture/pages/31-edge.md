# Edge

[Overview](00-overview.md) › [Chat backend](30-chat-backend.md) › Edge

The browser reaches ClaimClarity through one CloudFront distribution, so the web app and the API share one
origin. That means no CORS, so the session cookie can be SameSite=Strict, and WAF rules cover every request.
The web app is static files in S3. API calls go through an Application Load Balancer to the chat API tasks in
private subnets.

![Edge](../png/31-edge.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| – | Browser | Loads the web app, calls `/api/*` on the same origin | |
| 1 | AWS WAF | Web ACL on the distribution: managed rules, a rate rule per client address, a body size rule | [WAF card](../cards/waf.md) |
| 2 | CloudFront | TLS, routing by path, caching of hashed assets, a fresh CSP nonce on every HTML response | [CloudFront card](../cards/cloudfront.md) |
| 3 | Web app bucket | The built web app: hashed `/assets/*` and the `index.html` template. Holds no PHI. | [Deploy](61-deploy.md) |
| 4 | Load balancer | Application Load Balancer. Accepts traffic from CloudFront only and forwards it to the chat API tasks. | [ALB card](../cards/alb.md) |
| 5 | Chat API | ECS service on Fargate in private subnets. Checks the token on every call. | [Chat turn](32-chat-turn.md) |
| 6 | Amazon Cognito | Publishes the public keys that sign the id tokens. The chat API caches them. | [Token to credentials](22-token-to-credentials.md) |

## How it works

1. The browser opens `https://<ClaimClarity host>/`. AWS WAF checks every request before CloudFront serves it.
2. CloudFront routes by path:
   - `/assets/*` comes from the web app bucket through origin access control. These files are cached for one
     year (`public, max-age=31536000, immutable`).
   - `/` and every app route return `index.html`. A Lambda@Edge function on the origin request puts a fresh
     random nonce (at least 128 bits) in place of the build's nonce placeholder and sends the matching
     Content-Security-Policy. HTML is never cached (`no-store`).
   - `/api/*` goes to the load balancer with caching off. All headers, cookies and query strings are forwarded,
     and the response is streamed so SSE is not buffered.
3. The web app is built with Subresource Integrity, so every script and stylesheet carries a hash. Nothing on
   the edge may rewrite JS, CSS or HTML. Minify, script deferral and injection features stay off. gzip and
   brotli are fine.
4. The load balancer forwards to the chat API target group. CloudFront adds a secret origin header, and the ALB
   listener rule rejects requests without it. The ALB security group only accepts the CloudFront origin-facing
   prefix list.
5. The chat API checks the Cognito id token signature, audience, expiry and customer claim on every call
   ([Token to credentials](22-token-to-credentials.md)). It gets the signing keys from Cognito and caches them.

## Specification

### Routing

| Path | Origin | Cache | Notes |
|---|---|---|---|
| `/assets/*` | Web app bucket (origin access control) | 1 year, immutable | Hashed file names. Old files are kept for at least one cache lifetime so open tabs can still load lazy chunks. |
| `/`, app routes | Lambda@Edge reads the `index.html` template from the bucket | no-store | Fresh CSP nonce per response, full security header set |
| `/api/*` | Load balancer | off | Forward all; response streaming; SSE carries `X-Accel-Buffering: no` |
| a missing asset | Web app bucket | | 404, never falls back to `index.html` |

### Headers

| Where | Headers |
|---|---|
| HTML (edge) | CSP with nonce and Trusted Types: `default-src 'self'`, `script-src 'self' 'nonce-…'`, `connect-src 'self'`, `object-src 'none'`, `frame-ancestors 'none'`, `base-uri 'none'`; `Referrer-Policy: no-referrer`; `X-Content-Type-Options: nosniff`; `X-Frame-Options: DENY`; locked-down `Permissions-Policy`; COOP and CORP `same-origin`; HSTS |
| API (set by the chat API) | `Cache-Control: no-store`, `Pragma: no-cache`, deny-all CSP (`default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'; sandbox`), the same Referrer, nosniff, XFO, Permissions, COOP and CORP headers, `Strict-Transport-Security: max-age=63072000; includeSubDomains`, no `Server` header |

### Timeouts and limits

| Item | Value |
|---|---|
| SSE heartbeat | Every 15 s of silence. It must stay below every idle timeout on the path. |
| CloudFront origin read timeout | 60 s. The heartbeat keeps the connection busy. |
| ALB idle timeout | 60 s (the default) |
| Client stall detection | The web app reconnects after 45 s with no bytes |
| Request body | 1 MiB at the API (`413 payload_too_large`). The WAF body rule and the ALB allow at least this much, so the user gets the API's message. |
| WAF rate rule | Per client address over 5 minutes, answered with a 429 `rate_limited` custom response. See [Limits](34-limits.md). |

### Permissions

| Who | Can do |
|---|---|
| CloudFront (origin access control) | Read objects in the web app bucket. Nothing else. |
| Lambda@Edge role | Read the `index.html` template in the web app bucket. Write its own logs. |
| Load balancer | Reach the chat API tasks' port in the private subnets. Its security group allows only the CloudFront prefix list. |
| Chat API task role | No edge permissions. It fetches the Cognito signing keys over HTTPS. |

### Encryption, data and logging

- TLS 1.2 or higher from the browser to CloudFront and from CloudFront to the ALB. ACM certificates.
- The web app bucket holds no PHI and is encrypted with SSE-S3. It is a separate bucket from the ClaimClarity
  bucket, so the edge has no path to customer data.
- CloudFront and ALB access logs record full request paths. Paths carry only opaque conversation and download
  ids, never question text, but they are treated as PHI-bearing: encrypted, readable only by the security
  team, and kept no longer than the transcripts (90 days).
- The chat API's own access log records route shapes only (`/api/conversations/:id`) and truncates client
  addresses to /24 (IPv4) or /48 (IPv6).
- Credentials are never accepted in query strings, so URLs in logs never carry a token.

### Failure behaviour

- WAF block: the request never reaches an origin. A rate-rule block returns 429, which the client waits out.
- An unhealthy chat API task is taken out of the target group by the health check (`GET /api/health`).
- Lambda@Edge failure: HTML returns 5xx and the app does not load. Assets and API calls keep working.

## Related

Up: [Chat backend](30-chat-backend.md) · See also: [Sign-in](21-sign-in.md), [Network](51-network.md),
[Deploy](61-deploy.md), cards: [CloudFront](../cards/cloudfront.md), [WAF](../cards/waf.md),
[ALB](../cards/alb.md)
