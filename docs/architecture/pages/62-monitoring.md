# Monitoring

[Overview](00-overview.md) › [Operations](60-operations.md) › Monitoring

Every ClaimClarity service writes structured logs to CloudWatch Logs. The logs never carry PHI: no question
text, no SQL, no row values. Metrics come from three places: the logs (one metric record per answer, per
refusal and per parser batch), the AWS services themselves (SQS, ALB, ECS, Bedrock), and metric filters.
Alarms on those metrics notify on-call.

![Monitoring](../png/62-monitoring.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Chat API and run workers | Redacted application logs, plus one embedded-metric record per answer | [Chat turn](32-chat-turn.md) |
| 2 | Parser task | One record per batch: files parsed, failed and quarantined | [Parser task](14-parser-task.md) |
| 3 | Run queue | SQS metrics: messages visible and the age of the oldest message | [SQS card](../cards/sqs.md) |
| 4 | CloudWatch Logs | One log group per service, encrypted, with no PHI | [CloudWatch card](../cards/cloudwatch.md) |
| 5 | CloudWatch metrics | Latency, cost, outcomes, errors, queue and parser metrics | |
| 6 | CloudWatch alarms | The thresholds in the table below | |
| – | On-call | Receives alarm notifications through SNS (e-mail and chat) | |

## How it works

1. The chat API and the run workers log through a redaction filter. Free text in a log message is masked
   (identifiers, long digit runs, quoted strings). Exceptions are logged as the exception type plus file and
   line only. Access logs record route shapes (`/api/conversations/:id`) and client addresses cut to /24 or /48.
2. At the end of every answer the run worker writes one audit record (`chat_message`) with ids, counts and
   timings only: customer, user, conversation and message ids, outcome, number of queries and steps, time.
   The same record carries the answer's usage in embedded metric format: tokens (input, output, cache read
   and write), the estimated cost, time to first event and to first token, model time, SQL time, and the
   per-stage times (plan, sql, repair, checks, render).
3. The parser task writes one record per batch with files parsed, failed and quarantined.
4. SQS, ALB, ECS and Bedrock publish their own metrics.
5. Metric filters count specific log lines: rate-limit refusals, run-event chunk write failures, conditional
   write conflicts, data escalations, dropped charts and denied downloads.
6. Alarms watch the metrics and notify on-call through an SNS topic.

## Specification

### Log groups

| Log group | Source | Retention | PHI |
|---|---|---|---|
| `/claimclarity/chat-api` | Chat API tasks: application, audit and access logs | 1 year | No |
| `/claimclarity/run-worker` | Run worker tasks | 1 year | No |
| `/claimclarity/parser` | Parser tasks | 1 year | No |
| `/claimclarity/edge-html` | The Lambda@Edge HTML function | 90 days | No |

Log groups are encrypted with a KMS key for logs, which is not a customer key. CloudFront and ALB access logs
are kept separately, because their paths carry ids ([Edge](31-edge.md)).

### Metrics (namespace `ClaimClarity`)

| Metric | Dimensions | From |
|---|---|---|
| `Answers` by outcome (`stop`, `abstain`, `needs_input`, `error`, `aborted`) | customer | run worker |
| `TimeToFirstTokenMs`, `AnswerMs`, `ModelMs`, `SqlMs`, `QueueMs`, stage times | customer | run worker |
| `InputTokens`, `OutputTokens`, `CacheReadTokens`, `CostUsd` (estimate from the price table) | customer | run worker |
| `QueriesPerAnswer` | customer | run worker |
| `RateLimited` by reason | customer | chat API |
| `ChunkWriteFailures`, `ConditionalWriteConflicts` | service | metric filter |
| `DataEscalations` | customer | metric filter on `data_escalation` |
| `FilesParsed`, `ParseFailures`, `FilesQuarantined` | customer | parser |
| `ApproximateNumberOfMessagesVisible`, `ApproximateAgeOfOldestMessage` | queue | SQS |
| `HTTPCode_Target_5XX_Count`, `TargetResponseTime`, `HealthyHostCount` | target group | ALB |
| `InvocationLatency`, `InvocationThrottles` | model | Bedrock |

### Alarms (starting thresholds, reviewed after the first month)

| Alarm | Condition | Action |
|---|---|---|
| API errors | ALB target 5xx above 1 % of requests for 5 minutes | Page |
| API capacity | Healthy chat API targets below 2 for 2 minutes | Page |
| Answer errors | `error` outcomes above 5 % of answers for 15 minutes | Page |
| Slow first token | p95 time to first token above 15 s for 15 minutes | Notify |
| Model latency | Bedrock p95 invocation latency above 10 s, or any throttles, for 10 minutes | Notify |
| Daily cost | A customer's tokens above 80 % of its daily budget | Notify |
| Queue backlog | Age of the oldest run message above 30 s for 5 minutes | Page |
| Lost runs | Any message in the run queue's dead-letter queue | Notify |
| Worker capacity | Running worker tasks below desired for 5 minutes | Page |
| Chunk writes | Any run-event chunk write failure in 5 minutes | Notify |
| Parser failures | Any failed parser batch | Page |
| Quarantine | Any file quarantined | Notify the data team |
| Rate limiting | More than 50 rate-limit refusals for one customer in 15 minutes | Notify |

### Permissions

| Who | Can do |
|---|---|
| Task roles (chat API, run worker, parser) | Create log streams and put log events in their own log group. Put metric data only in the `ClaimClarity` namespace. |
| Operations team | Read logs, metrics and dashboards. Logs hold no PHI, so this needs no customer access. |
| SNS topic | Publish from CloudWatch alarms only. Encrypted with the logs key. |

### What is never logged

Question text, answer text, SQL text, row values, table previews, titles, feedback comments, tokens and
cookies. The agent audit holds the SQL text, but it is stored as write-once records under the customer's prefix,
not in CloudWatch ([Audit & logging](53-audit-logging.md)).

## Related

Up: [Operations](60-operations.md) · See also: [Limits](34-limits.md), [Audit & logging](53-audit-logging.md),
card: [CloudWatch](../cards/cloudwatch.md)
