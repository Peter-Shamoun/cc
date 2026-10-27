### AWS CloudTrail
| | |
|---|---|
| Purpose in ClaimClarity | The record of every AWS call: who (role and session name), what (action, object, key), and whether it was allowed. The AWS half of the audit; the agent audit is the SQL half |
| Talks to | The trail log bucket, CloudWatch Logs (alarms) |
| Permissions | Only the deploy role changes the trail. Only the security team's audit role reads the trail log bucket |
| Encryption | Trail logs encrypted with a dedicated KMS key. Log file validation on |
| Data held / PHI | Object keys, role and session names, request parameters. Object keys contain dates and file names, not patient data. No object contents |
| Scaling and limits | Management events plus S3 data events (read and write) for the ClaimClarity bucket. Data events are billed per event |
| Failure behaviour | Delivery is retried by AWS. A stopped trail or a deleted trail raises an alarm |
| Logging and audit | Trail log bucket with Object Lock COMPLIANCE, 6 years. Digest files allow integrity checks |
| Used on pages | [Token to credentials](22-token-to-credentials.md) · [Customer isolation](23-customer-isolation.md) · [Audit & logging](53-audit-logging.md) |
