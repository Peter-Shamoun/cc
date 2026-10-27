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
| Logging and audit | Alarm and dashboard changes are in CloudTrail. Alarm thresholds are listed on [Monitoring](../pages/62-monitoring.md). |
| Used on pages | [Operations](../pages/60-operations.md), [Monitoring](../pages/62-monitoring.md), [Limits](../pages/34-limits.md) |
