### AWS WAF
| | |
|---|---|
| Purpose in ClaimClarity | Filters every request at CloudFront before it reaches the web app or the chat API |
| Talks to | Attached to the CloudFront distribution. Sends logs to CloudWatch Logs. |
| Permissions | Only the infrastructure pipeline may change the web ACL. The operations team may read its metrics and logs. |
| Encryption | Not applicable (inline inspection). Its log group is encrypted with the logs key. |
| Data held / PHI | None stored. WAF logs are set to redact the `cookie` and `authorization` headers, and they never record request bodies. |
| Scaling and limits | AWS managed rule groups (common rule set, known bad inputs, IP reputation). A body size rule that allows at least the API's 1 MiB limit. A rate-based rule per client address over 5 minutes, answered with a 429 custom response (`{"error":{"code":"rate_limited"}}` and `Retry-After`). |
| Failure behaviour | A blocked request never reaches an origin. A rate-limit block returns 429, which the web app waits out and retries twice before it shows "Too many requests". |
| Logging and audit | Sampled requests and full logs (with headers redacted) in CloudWatch Logs. `BlockedRequests` and `CountedRequests` metrics per rule. Web ACL changes are recorded in CloudTrail. |
| Used on pages | [Edge](../pages/31-edge.md), [Limits](../pages/34-limits.md) |
