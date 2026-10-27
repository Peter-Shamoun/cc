# Open decisions

[Overview](00-overview.md) › Open decisions

The pages are written with the default shown here. Each can be changed in one place.

| # | Question | Default used in these pages | Why it is open |
|---|---|---|---|
| 1 | SFTP: AWS Transfer Family or an SFTP host on EC2? | Transfer Family | The reference infrastructure has IT's SFTP host on EC2 with a copy script. |
| 2 | Raw-zone write rule vs Transfer Family | Transfer Family lands files in a landing prefix. A small copy step writes them into `raw/` as create-only, with Object Lock retention and the customer's KMS key. | The bucket policy demands those three on every raw write, which Transfer Family uploads don't set. |
| 3 | Does the agent audit record hold the SQL text? | Yes, the first 500 characters, stored with the customer's run data under the customer's audit key. Server logs never hold SQL. | The API contract says ids, counts and timings only; the code records SQL. |
| 4 | EventBridge retry policy for the parser launch | 3 attempts, then the dead-letter queue | The AWS default retries for 24 hours. |
| 5 | Parser task credentials | Task-role credentials can outlive the task (up to about 6 hours). The boundary is the role's permissions, not the container's lifetime. | ECS behaviour. Documented as it is. |
| 6 | Reader credentials in long chats | Renewed with the same ID token when fewer than 3 minutes remain | Not yet in the code. |
| 7 | Internet egress for the chat API | NAT gateway + Network Firewall, allowing only the Cognito hosts (token keys, sign-in callback) | Alternative: no NAT, ship the token keys with each deploy. |
| 8 | Log retention | Network and app logs 1 year, CloudTrail 6 years. From the code: raw 7 years, audit 6 years, transcripts 90 days. | The 1-year and 6-year values are proposals. |
| 9 | Run limits | Run deadline 110 s. Per-customer concurrency and token budgets are settings, with values to be chosen. | Not in the code. |
| 10 | Edge | A Lambda@Edge function sets a fresh security-policy nonce on every page | Static hosting alone can't do it. |
| 11 | Where the landing → raw copy step runs | A Lambda function triggered by the lab's `_SUCCESS` in `landing/`, running as the customer's SFTP copy role | Neither the code nor the design names its compute. |
| 12 | Cookie and header names (`cc_session`, `x-cc-csrf`) | Kept as they are in the code, because IT configures WAF and log redaction with them | They still carry the old "CC" name. |
| 13 | Runs on SQS and run workers | These pages describe the SQS queue and Fargate run workers. They also describe the cancel flag, limits object, CSV objects and event chunks that design needs. | Today the chat API runs each question in its own process. |
