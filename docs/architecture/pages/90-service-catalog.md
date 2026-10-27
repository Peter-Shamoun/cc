# AWS service catalog

[Overview](00-overview.md) › Service catalog

One card per AWS service ClaimClarity uses: what it does in ClaimClarity, what it talks to, who may do what, encryption, data held, limits, failure behaviour and logging. Each card links to the pages that use it.

### AWS Transfer Family
| | |
|---|---|
| Purpose in ClaimClarity | The SFTP endpoint where customer labs deliver their daily 837P, 835 and 277 files |
| Talks to | Customer SFTP clients (SSH); Amazon S3 (writes into the customer's `landing/` prefix) |
| Permissions | One user per customer, signed in with the customer's SSH key. The user's home folder maps to `<customer>/landing/` only, written through the customer's Transfer Family role (put only). A copy step, as the customer's SFTP copy role, then writes each file into `raw/` create-only, with Object Lock and the customer's lake key, and writes `_SUCCESS` last. |
| Encryption | SSH in transit; SSE-KMS with the customer's lake key at rest. The bucket policy refuses a raw write that is not create-only with Object Lock COMPLIANCE retention of at least 2,555 days. |
| Data held / PHI | Passes full PHI through; stores nothing itself |
| Scaling and limits | Managed service; one internet-facing server for all customers (TCP 22, lab source addresses only), one user per customer |
| Failure behaviour | A day without the lab's `_SUCCESS` is never copied or parsed. A name that already exists in raw is refused; the lab sends a corrected file under a new name. |
| Logging and audit | Session logs (user, connect, file names) in CloudWatch Logs; CloudTrail records each S3 write and KMS call |
| Used on pages | [SFTP intake](11-sftp-intake.md) · [Ingestion & lake](10-ingestion.md) |

### Amazon S3
| | |
|---|---|
| Purpose in ClaimClarity | The ClaimClarity bucket holds all customer data: each customer's lake (landing, raw, silver, gold, staging, quarantine) and chat state, one prefix per customer. The web app and the access logs have their own buckets. |
| Talks to | AWS Transfer Family (writes landing as the customer's Transfer Family role), the copy step (writes raw), EventBridge (Object Created events), the parser task (reads raw, writes the derived zones), the run workers (read gold), KMS (SSE-KMS on every object) |
| Permissions | Each customer's SFTP copy role moves files from `landing/` into `raw/` and cannot change raw. Its parser role reads raw and reads and writes `staging/`, `quarantine/`, `parquet/`, `semantic_layer/`. Its reader role reads `semantic_layer/` only. The bucket policy (one document for all customers) denies: non-TLS requests; principals without a `customer` tag; object access outside `${aws:PrincipalTag/customer}/`; listing outside that prefix; writes with any key but the principal's `kms_key_arn`; raw writes that are not create-only, COMPLIANCE-locked for 2,555 days; any raw delete. Only the Terraform deploy role may change bucket policy, lifecycle, versioning, Object Lock or encryption. |
| Encryption | SSE-KMS with the customer's lake key on every lake object (transcripts and the query audit use their own keys); the bucket default (platform) key is refused for customer prefixes. TLS only. |
| Data held / PHI | Raw, silver and quarantine: full PHI. Gold: no direct patient identifiers. |
| Scaling and limits | Versioning and Object Lock on. Lifecycle: noncurrent versions expire after 1 day (`semantic_layer/`, `staging/`) or 7 days (`parquet/`); `quarantine/` objects after 1 year; none for `raw/`. The bucket policy stays the same size for any number of customers. |
| Failure behaviour | Conditional writes (`If-None-Match`, `If-Match`) back the batch lease and the `CURRENT` compare-and-set; a lost race fails cleanly and nothing half-written becomes visible. |
| Logging and audit | CloudTrail S3 data events for every object call, allowed or denied; KMS calls logged separately |
| Used on pages | [Ingestion & lake](10-ingestion.md) · [S3 layout](12-s3-layout.md) · [SFTP intake](11-sftp-intake.md) · [Parser task](14-parser-task.md) · [Gold publish](15-gold-publish.md) |

### Amazon EventBridge
| | |
|---|---|
| Purpose in ClaimClarity | Starts one parser task when a customer's `_SUCCESS` marker lands in raw |
| Talks to | Amazon S3 (Object Created events from the ClaimClarity bucket); Amazon ECS (RunTask); Amazon SQS (dead-letter queue) |
| Permissions | One rule per customer, matching only `<customer>/raw/received_date=*/_SUCCESS` in the ClaimClarity bucket (gate 1). Its target role, the launcher, may call `ecs:RunTask` on the parser task definitions in the ClaimClarity cluster and `iam:PassRole` on the parser roles to ECS tasks only (gate 2). It has no S3 or KMS access. |
| Encryption | Events carry object keys, not file contents |
| Data held / PHI | None. Object keys hold the customer prefix and received date, never patient data. |
| Scaling and limits | Delivery is at least once, so a duplicate event can start a second task; the parser's batch lease and skip of loaded files make that harmless |
| Failure behaviour | Retries a failed RunTask; after 3 attempts the event goes to the SQS dead-letter queue, from which a day can be redriven |
| Logging and audit | CloudTrail records RunTask, PassRole and failed invocations; the event id is stored with the batch in `edi.ingest_run` |
| Used on pages | [Parse trigger](13-parse-trigger.md) · [Ingestion & lake](10-ingestion.md) · [SFTP intake](11-sftp-intake.md) |

### Amazon ECS on AWS Fargate
| | |
|---|---|
| Purpose in ClaimClarity | Runs all ClaimClarity compute as three task types, with no EC2 hosts: the **chat API service**, the **run worker service** and the **parser task**. |
| Talks to | **Chat API:** the ALB (inbound), Cognito signing keys (through the NAT gateway and Network Firewall, Cognito hosts only), the run queue (send), the ClaimClarity bucket (conversations, run-event chunks, cancel flags, limits object), KMS. **Run worker:** the run queue (receive, delete), STS (AssumeRoleWithWebIdentity with the user's token), the ClaimClarity bucket (gold reads, chunks, answers, agent audit), KMS, Amazon Bedrock. Its egress to Bedrock goes through a VPC endpoint. **Parser task:** started by the launcher for one batch. It reads raw, writes lossless and gold under one customer's prefix, and publishes the views ([Parser task](14-parser-task.md)). All three use VPC endpoints for AWS APIs. |
| Permissions | **Task execution role** (all three): pull its own image from ECR, write its own log group. **Chat API task role:** send to the run queue. It holds no customer data permissions of its own: S3 and KMS calls use the signed-in user's session credentials: Customer B's transcript role, tagged with the user. **Run worker task role:** receive and delete on the run queue, sts:AssumeRoleWithWebIdentity, sts:AssumeRole on the audit writer roles, Bedrock InvokeModel for Claude Haiku only. Data access uses the customer's roles and keys from the token: Customer B's reader role for gold ([gates 5 and 6](22-token-to-credentials.md)), the transcript role for chat state, and the audit writer role for audit records. **Parser task role:** Customer B's parser role, passed by the launcher ([gate 2](13-parse-trigger.md)). |
| Encryption | Ephemeral task storage encrypted by Fargate. All traffic is TLS. Data at rest is in S3 under SSE-KMS with the customer's keys (lake key for gold, transcript key for chat state). Message text is also sealed with the per-user data key. |
| Data held / PHI | Chat API: PHI in memory only while a request runs, plus temporary chats (1 hour idle, at most 20 per user). Run worker: PHI in memory for one run. The DuckDB session has no temp directory, so nothing is spilled to disk. Parser: one batch in memory and in task storage, gone when the task ends. |
| Scaling and limits | **Chat API:** at least 2 tasks across two Availability Zones. Scales on CPU and request count. One process per task. **Run worker:** scales on queue depth per task (target 1 message per task). One run per worker at a time. Run deadline 110 s, inside the 120 s stop timeout. Reader credentials are renewed with the same ID token when fewer than 3 minutes remain. **Parser:** one task per batch, sized per batch type. Rolling deploys keep at least 100 % healthy, with the circuit breaker and rollback on. |
| Failure behaviour | **Chat API:** an unhealthy task leaves the target group, and clients reconnect to another task and replay from S3. **Run worker:** a run is not retried (the queue's maximum receive count is 1, then the dead-letter queue). Its stream stops, the client reloads, and the user retries. **Parser:** a bad file is quarantined; a failed batch commits nothing, raises the parser alarm and is re-run. |
| Logging and audit | Each service logs to its own CloudWatch log group through the redaction filter, with no PHI. Audit events carry ids, counts and timings. The run worker writes one agent-audit record per SQL call under the customer's prefix, through the customer's audit writer role. ECS API calls are recorded in CloudTrail. |
| Used on pages | [Chat backend](30-chat-backend.md), [Edge](31-edge.md), [Chat turn](32-chat-turn.md), [Limits](34-limits.md), [Deploy](61-deploy.md), [Monitoring](62-monitoring.md) |

### Amazon ECR
| | |
|---|---|
| Purpose in ClaimClarity | Stores the container images for the chat API, the run worker and the parser |
| Talks to | CI (push, through the deploy role). ECS task execution roles (pull). Accessed through VPC endpoints from the private subnets. |
| Permissions | Only the deploy role may push, and only to these three repositories. Each task execution role may pull only its own repository. Nobody may delete a tagged image except the lifecycle policy. |
| Encryption | KMS encryption at rest. TLS in transit. |
| Data held / PHI | Code and dependencies only. No PHI, no secrets, no customer data, and no model credentials in any image. |
| Scaling and limits | Immutable tags (the commit SHA). Task definitions pin the image digest. Scan on push, and a critical finding stops the deploy. The lifecycle policy keeps the last 50 images per repository, enough to roll back. |
| Failure behaviour | If ECR cannot be reached, new tasks cannot start, but running tasks keep running. The ECS circuit breaker rolls back a deploy whose tasks cannot pull. |
| Logging and audit | Push and pull calls are in CloudTrail. Scan findings are reported to the security team. |
| Used on pages | [Operations](60-operations.md), [Deploy](61-deploy.md) |

### Amazon SQS (run queue)
| | |
|---|---|
| Purpose in ClaimClarity | Decouples accepting a question from running it. The chat API queues one message per admitted run, and a free run worker takes it. |
| Talks to | Chat API (SendMessage). Run workers (ReceiveMessage with 20 s long polling, DeleteMessage). A dead-letter queue. CloudWatch (metrics). |
| Permissions | The queue policy allows SendMessage only from the chat API task role, and Receive, Delete and ChangeMessageVisibility only from the run worker task role. Access goes through a VPC endpoint, and the policy denies requests from outside it. |
| Encryption | SSE-KMS with a key for the queue. TLS in transit. |
| Data held / PHI | Ids only (message, conversation, customer, user hash) plus the user's Cognito id token, which the worker needs for the STS exchange. Never the question text, which the worker reads from the conversation object. The token is a credential: never logged, and it expires on its own. |
| Scaling and limits | Standard queue. Visibility timeout 150 s (above the 110 s run deadline). Message retention 5 minutes. A worker ends a run as an error without running it if it waited more than 2 minutes. Maximum receive count 1, then the dead-letter queue. Admission per customer happens before SendMessage, so the queue cannot be flooded by one customer. |
| Failure behaviour | A worker that dies leaves its message invisible until the timeout. It then moves to the dead-letter queue and is not run again, because a rerun would repeat model calls and SQL. The user sees the stream stop, the saved conversation reloads, and Retry runs the question again. |
| Logging and audit | `ApproximateNumberOfMessagesVisible` and `ApproximateAgeOfOldestMessage` drive worker scaling and alarms. Any message in the dead-letter queue raises an alarm. Queue configuration changes are recorded in CloudTrail. |
| Used on pages | [Chat backend](30-chat-backend.md), [Chat turn](32-chat-turn.md), [Limits](34-limits.md), [Monitoring](62-monitoring.md) |

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
| Used on pages | [Edge](31-edge.md), [Deploy](61-deploy.md), [Chat backend](30-chat-backend.md) |

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
| Used on pages | [Edge](31-edge.md), [Limits](34-limits.md) |

### Elastic Load Balancing (Application Load Balancer)
| | |
|---|---|
| Purpose in ClaimClarity | Spreads `/api/*` traffic from CloudFront across the chat API tasks, and takes unhealthy tasks out of service |
| Talks to | CloudFront (inbound, HTTPS). The chat API target group (ECS on Fargate, private subnets). |
| Permissions | Its security group allows inbound HTTPS only from the CloudFront origin-facing managed prefix list. A listener rule also requires the secret origin header that CloudFront adds. Its outbound traffic may reach only the chat API tasks' port. |
| Encryption | HTTPS listener with an ACM certificate, and HTTPS (or TLS) to the targets. |
| Data held / PHI | None stored. Requests and SSE responses, which can hold PHI, pass through. |
| Scaling and limits | Idle timeout 60 s, which is above the 15 s SSE heartbeat. Health check `GET /api/health`, which must return 200. Deregistration delay 120 s, so open streams can drain during a deploy. Stickiness is on (load-balancer cookie, 1 hour), only so a temporary chat, which lives in one task's memory, keeps its follow-ups. Saved chats do not depend on it: their run state is in S3, so any task can serve them. |
| Failure behaviour | A failing target is removed. If a stream is cut during a drain, the browser reconnects with `Last-Event-ID` and replays from the stored run events. |
| Logging and audit | Access logs go to the access log bucket (separate from the ClaimClarity bucket) (they show full paths with opaque ids: PHI-bearing, encrypted, 1 year). CloudWatch metrics: 5xx count, response time, healthy hosts. |
| Used on pages | [Edge](31-edge.md), [Monitoring](62-monitoring.md) |

### Amazon Cognito
| | |
|---|---|
| Purpose in ClaimClarity | User pool federated to Microsoft Entra ID (its only identity provider). Maps each user to one customer and issues the id token that names it |
| Talks to | Entra ID (OIDC), the chat API sign-in callback (token endpoint), STS (via the IAM OIDC provider) |
| Permissions | Administrators set `custom:customer` (and `custom:transcript_admin` for customer admins). The app client cannot write these attributes. A pre-authentication check refuses users with no customer. A pre-token-generation step (v2) adds the session tags `customer`, `user` (= `sub`) and, for customer admins, `transcript_admin`, all transitive |
| Encryption | Tokens signed with RS256. All traffic over HTTPS |
| Data held / PHI | User email, Entra object id, customer mapping. No PHI |
| Scaling and limits | Id token 60 minutes. Refresh token 30 days, kept server-side and not given to the browser. Users are created just in time on first sign-in |
| Failure behaviour | No customer mapping: no token. Disabled user: no new sign-ins. Global sign-out revokes refresh tokens; issued id tokens stay valid until they expire |
| Logging and audit | Cognito API calls in CloudTrail. The chat API never logs tokens. See [Sign-in](21-sign-in.md) |
| Used on pages | [Identity & access](20-identity.md) · [Sign-in](21-sign-in.md) · [Token to credentials](22-token-to-credentials.md) · [Network](51-network.md) |

### Microsoft Entra ID (external)
| | |
|---|---|
| Purpose in ClaimClarity | The company login. One enterprise app for ClaimClarity with "Assignment required" on: only members of the assigned groups can sign in |
| Talks to | Amazon Cognito (OIDC identity provider for the user pool), the user's browser |
| Permissions | IT assigns groups to the app. A separate group marks customer administrators. MFA and Conditional Access follow company policy |
| Encryption | HTTPS. Tokens signed by the tenant's keys and verified by Cognito (issuer, tenant, app client id) |
| Data held / PHI | Company accounts and group membership. No PHI |
| Scaling and limits | Entra id token 60 minutes. The Entra browser session makes re-sign-in after the Cognito token expires a single redirect |
| Failure behaviour | Unassigned user: AADSTS50105. Disabled account: AADSTS50057. Unknown account: AADSTS50034. No ClaimClarity token is issued in any of these cases |
| Logging and audit | Entra sign-in log (successes and refusals), kept per company policy |
| Used on pages | [Identity & access](20-identity.md) · [Sign-in](21-sign-in.md) · [Customer isolation](23-customer-isolation.md) |

### AWS STS
| | |
|---|---|
| Purpose in ClaimClarity | Exchanges a user's Cognito id token for 15-minute credentials of that customer's reader role (AssumeRoleWithWebIdentity, gate 5). Also issues the AssumeRole sessions for the audit writer, the SFTP copy and support break-glass |
| Talks to | IAM (the OIDC provider for the Cognito user pool, role trust policies), the run workers through the STS interface endpoint |
| Permissions | A reader role's trust policy allows `sts:AssumeRoleWithWebIdentity` and `sts:TagSession` only for the ClaimClarity Cognito OIDC provider, only when `aud` is the ClaimClarity app client and `aws:RequestTag/customer` equals that role's customer. The audit writer trusts only the run worker task role, with matching `customer` and a `user` tag. Break-glass trusts only the support role, with an external id, a `TICKET-*` reason tag, a customer tag and an operator tag |
| Encryption | HTTPS through the VPC interface endpoint |
| Data held / PHI | None. Session tags carry the customer and the Cognito `sub` |
| Scaling and limits | One call per run, plus one renewal per 12 minutes of a long run. Requested duration 900 s. Role maximum session duration is the AWS minimum, 3600 s. Role chaining is capped at 1 hour by AWS |
| Failure behaviour | `AccessDenied`, `InvalidIdentityToken` or `ExpiredTokenException` end the run with a sign-in-again message. Throttling is retried 3 times with backoff. "Revoke active sessions" on a role denies every session issued before that moment |
| Logging and audit | Every AssumeRole and AssumeRoleWithWebIdentity, allowed or denied, is in CloudTrail with the role and the session name `u-<sub prefix>-<request id>` |
| Used on pages | [Identity & access](20-identity.md) · [Token to credentials](22-token-to-credentials.md) · [Customer isolation](23-customer-isolation.md) · [Network](51-network.md) |

### AWS IAM
| | |
|---|---|
| Purpose in ClaimClarity | Defines every role and what it may do. Each customer has its own lake roles (Transfer Family, SFTP copy, parser, reader), transcript roles (user, customer admin, eval) and audit roles (writer, reader), each tagged `customer = <customer>` and `kms_key_arn = <that role's key>`. Platform roles: chat API, run workers, deploy (Terraform), key admin, support and support break-glass, eval team, and the EventBridge launcher. |
| Talks to | STS (role trust policies), S3 (identity policies plus the ABAC bucket policy), KMS (key policies name the roles), ECS (task roles, `iam:PassRole` for the launcher, gate 2) |
| Permissions | Reader: `ListBucket` (prefix `<customer>/semantic_layer/*`) and `GetObject` on `<customer>/semantic_layer/*` only, with no `GetObjectVersion`. Parser: reads raw and the derived layers, writes staging, silver, gold and quarantine of its customer. Transfer Family: `PutObject` on `<customer>/landing/*`. SFTP copy: `GetObject` and `DeleteObject` on `<customer>/landing/*`, `PutObject` and `PutObjectRetention` on `<customer>/raw/*`. Audit writer: `PutObject` only on `<customer>/audit/queries/*`. Run worker task role: assumes the audit writer roles; it reads data only through the customer's roles. Chat API role: no customer data permissions of its own. Launcher: `ecs:RunTask` for the parser task definitions on the ClaimClarity cluster, and `iam:PassRole` for the parser roles to `ecs-tasks` only. Only the deploy role changes IAM. |
| Encryption | n/a. Roles carry the `kms_key_arn` tag that the bucket policy compares against every PUT |
| Data held / PHI | None |
| Scaling and limits | Roles grow linearly with customers (9 per customer). The bucket policy is the same size for any number of customers (ABAC on the `customer` tag). The query-audit key policy grows by 2 statements per customer, within the 32 KB key policy limit for several hundred customers |
| Failure behaviour | Any missing permission is an explicit `AccessDenied`, never a silent fallback. Policies that name roles that do not exist are rejected when written |
| Logging and audit | Every IAM change is a CloudTrail management event. Role changes outside the deploy pipeline raise an alarm |
| Used on pages | [Identity & access](20-identity.md) · [Token to credentials](22-token-to-credentials.md) · [Customer isolation](23-customer-isolation.md) · [Encryption & keys](52-encryption-keys.md) |

### AWS KMS
| | |
|---|---|
| Purpose in ClaimClarity | Customer managed keys: one lake key per customer, one transcript key per customer, one query-audit key for the platform, platform keys for logs, the trail, the queues and secrets, and a platform default key that is never valid for customer objects. Disabling a customer's keys makes all of its objects unreadable at once |
| Talks to | S3 (SSE-KMS through `kms:ViaService`), the chat API and run workers (per-user transcript data keys), SQS, CloudWatch Logs and Secrets Manager (their own encryption) |
| Permissions | Key policies are authoritative and do not delegate to the account, so IAM policies cannot widen them. Lake key: `Decrypt` and `GenerateDataKey` for the customer's parser, reader and SFTP copy roles (and GenerateDataKey for its Transfer Family role on `landing/`) only, via S3 only, for objects under `<customer>/` only. Transcript key: user, customer admin, eval and break-glass statements, each limited by prefix or encryption context. Query-audit key: one writer and one reader statement per customer. Deploy role: `PutKeyPolicy` and lifecycle, no data use. Key admin: enable, disable, rotate, schedule deletion; no decrypt, no grants, no policy changes |
| Encryption | AES-256 keys held in KMS. S3 Bucket Keys are off, so each object's encryption context is its own ARN |
| Data held / PHI | Key material only |
| Scaling and limits | One `GenerateDataKey` per object written and one `Decrypt` per object read. Well within KMS request quotas at ClaimClarity's volumes. Key policy size 32 KB |
| Failure behaviour | Disabled or pending-deletion key: every read and write under it fails. Deletion waits 7–30 days and can be cancelled. A wrong encryption context fails to decrypt |
| Logging and audit | Every `Decrypt` and `GenerateDataKey` is a CloudTrail event with the role and the object ARN. Policy changes, disables and deletion schedules raise alarms |
| Used on pages | [Token to credentials](22-token-to-credentials.md) · [Customer isolation](23-customer-isolation.md) · [Encryption & keys](52-encryption-keys.md) · [Audit & logging](53-audit-logging.md) |

### Amazon Bedrock

| | |
|---|---|
| Purpose in ClaimClarity | The only model service. Runs Claude Haiku for the analyzer: the KPI gate, the reply writer for preset KPI queries, and every step of the Claim Query Agent. |
| Talks to | Called only by the run workers (ECS on Fargate), through the Strands `BedrockModel` (Converse streaming API). Bedrock calls nothing in ClaimClarity and has no access to the ClaimClarity bucket. |
| Permissions | The run worker's task role may invoke the Claude Haiku model (its inference profile and the foundation model behind it), streaming and non-streaming. No other role, model or Bedrock feature (agents, knowledge bases, fine-tuning) is allowed. The chat API and the parser cannot call Bedrock. |
| Encryption | TLS in transit. Bedrock does not store prompts or completions; model invocation logging is off. |
| Data held / PHI | Prompts contain the question, earlier questions of the conversation and query results (up to 20 rows per query, identifier columns redacted), so they are PHI. Nothing is stored after the call except the prompt cache, which keeps the shared prompt prefixes in memory for 5 minutes after their last use. Inputs and outputs are not used to train models and are not shared with the model provider. Bedrock is a HIPAA-eligible service under the AWS BAA. |
| Scaling and limits | On-demand, within the account's Bedrock quotas (requests and tokens per minute for the model). Per call: 4,096 output tokens. Per turn: 2 calls for a preset answer; 1 + one per step for an agent answer, at most 30 agent steps. |
| Failure behaviour | A failed or timed-out KPI gate call sends the turn to the Claim Query Agent. A failed reply-writer call does the same. A failed agent call ends the turn with a retryable error. The user's Stop aborts the in-flight stream. |
| Logging and audit | CloudTrail records the Bedrock API calls (caller role, model, time), without prompt content. Each run records token usage per model (input, output, cache read, cache write), model time and stop reason. |
| Used on pages | [Analyzer](40-analyzer.md) · [KPI gate](41-kpi-gate.md) · [Preset KPI queries](42-standard-kpi-path.md) · [Claim Query Agent](43-custom-analysis-agent.md) · [Model calls](45-model-calls.md) |

### Amazon VPC (including VPC endpoints)
| | |
|---|---|
| Purpose in ClaimClarity | Private network for the internal ALB and all ECS tasks. Two Availability Zones. No task has a public IP or a route to the internet gateway |
| Talks to | CloudFront (VPC origin to the internal ALB), AWS services through endpoints, Cognito through the single filtered egress |
| Permissions | Security groups per tier: ALB (443 from CloudFront's origin-facing prefix list), chat API (from the ALB only), run workers and parser (no inbound), endpoints (443 from the three task groups). Endpoint policies limit each endpoint to ClaimClarity resources |
| Encryption | All traffic is TLS. Endpoints keep AWS traffic on the AWS network |
| Data held / PHI | None at rest. PHI passes between tasks and S3 through the gateway endpoint |
| Scaling and limits | Endpoints: S3 (gateway); STS, KMS, Bedrock runtime, ECR api, ECR dkr, CloudWatch Logs, SQS, Secrets Manager (interface, private DNS on, one network interface per AZ). Egress: one NAT gateway per AZ behind AWS Network Firewall |
| Failure behaviour | An endpoint is per AZ, so one AZ failing leaves the other working. If the egress path fails, signed-in users continue on the cached Cognito keys; new sign-ins fail |
| Logging and audit | VPC flow logs for every subnet, Network Firewall alert and flow logs, in CloudWatch Logs for 1 year |
| Used on pages | [Network & security](50-network-security.md) · [Network](51-network.md) |

### AWS Secrets Manager
| | |
|---|---|
| Purpose in ClaimClarity | Holds the Cognito app client secret that the chat API uses for the code exchange at sign-in. There are no AWS access keys to store: every task uses its task role |
| Talks to | The chat API, through the Secrets Manager interface endpoint |
| Permissions | Only the chat API task role may `GetSecretValue`, on the ClaimClarity secrets only. Only the deploy role writes or rotates them |
| Encryption | A ClaimClarity KMS key |
| Data held / PHI | Secrets only. No PHI |
| Scaling and limits | Read at task start and cached in memory. Rotation is done by the deploy pipeline together with the Cognito app client |
| Failure behaviour | If the secret cannot be read at start, the chat API task fails its health check and does not take traffic |
| Logging and audit | Every `GetSecretValue` is a CloudTrail event. Secret values are never logged |
| Used on pages | [Sign-in](21-sign-in.md) · [Network](51-network.md) |

### Amazon CloudWatch
| | |
|---|---|
| Purpose in ClaimClarity | Logs, metrics and alarms for every service: chat API, run workers, run queue, parser, edge, Bedrock |
| Talks to | ECS tasks (logs and embedded metrics). SQS, ALB, ECS, WAF and Bedrock (their own metrics). An SNS topic for alarm notifications to on-call. |
| Permissions | Each task role may write only its own log group and put metrics only in the `ClaimClarity` namespace. The operations team may read logs, metrics and dashboards. No customer role has any CloudWatch access. |
| Encryption | Log groups are encrypted with a KMS key for logs, separate from every customer key. |
| Data held / PHI | No PHI. Application logs pass a redaction filter. The audit log is an allowlist of ids, counts and timings. Access logs record route shapes and truncated client addresses. Question text, answer text, SQL and row values are never logged. |
| Scaling and limits | Log retention: 1 year for service logs, 90 days for the edge function. Metrics carry a `customer` dimension (an id, never a name), so per-customer alarms and cost views are possible. |
| Failure behaviour | Logging is asynchronous. A logging outage never blocks an answer. Alarms treat missing data as breaching for the queue age and healthy-host alarms. |
| Logging and audit | Alarm and dashboard changes are in CloudTrail. Alarm thresholds are listed on [Monitoring](62-monitoring.md). |
| Used on pages | [Operations](60-operations.md), [Monitoring](62-monitoring.md), [Limits](34-limits.md) |

### AWS CloudTrail
| | |
|---|---|
| Purpose in ClaimClarity | The record of every AWS call: who (role and session name), what (action, object, key), and whether it was allowed. The AWS half of the audit; the agent audit is the SQL half |
| Talks to | The log bucket (CloudTrail prefix), CloudWatch Logs (alarms) |
| Permissions | Only the deploy role changes the trail. Only the security team's audit role reads the CloudTrail prefix |
| Encryption | Trail logs encrypted with the trail key, a dedicated platform KMS key. Log file validation on |
| Data held / PHI | Object keys, role and session names, request parameters. Object keys contain dates and file names, not patient data. No object contents |
| Scaling and limits | Management events plus S3 data events (read and write) for the ClaimClarity bucket. Data events are billed per event |
| Failure behaviour | Delivery is retried by AWS. A stopped trail or a deleted trail raises an alarm |
| Logging and audit | CloudTrail prefix of the log bucket with Object Lock COMPLIANCE, 6 years. Digest files allow integrity checks |
| Used on pages | [Token to credentials](22-token-to-credentials.md) · [Customer isolation](23-customer-isolation.md) · [Audit & logging](53-audit-logging.md) |
