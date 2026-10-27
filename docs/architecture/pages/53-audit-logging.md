# Audit logging

[Overview](00-overview.md) › [Network & security](50-network-security.md) › Audit logging

CloudTrail records which AWS identity touched which object or key, the agent audit records which SQL the analyzer
ran for whom, and the application logs record what the service did. The first two are kept write-once for 6 years;
the application logs are redacted so they carry no PHI.

![Audit logging](../png/53-audit-logging.png)

## In this diagram

| Item | What it does | Details |
|---|---|---|
| Every role | Any task role, pipeline, IT's SFTP copy or person making an AWS call. | [IAM and KMS matrix](91-iam-kms-matrix.md) |
| CloudTrail | Records management events and S3 data events, allowed or denied. | [CloudTrail card](90-service-catalog.md) |
| S3 · trail logs | The log bucket: Object Lock, its own KMS key, 6 years. | |
| Run worker | Runs every analyzer SQL call through one entry point. | [SQL guard & escalation](44-sql-guard-escalation.md) |
| Agent audit | One hash-chained record per SQL call and per session opening. | |
| S3 · B's query audit | `B/audit/queries/<date>/<request id>.json`, write-once for 6 years. | [Encryption keys](52-encryption-keys.md) |
| Chat API + workers | Write application and access logs. | |
| PHI log filter | Redacts every log record before it is written. | |
| CloudWatch Logs | KMS-encrypted log groups, kept 1 year. | [Monitoring](62-monitoring.md) |

## How it works

1. CloudTrail records every management event (IAM, STS, KMS, ECS, Cognito) and S3 data events for the
   ClaimClarity bucket, including denials. It delivers to the log bucket with log file validation on.
2. Each analyzer SQL call writes one agent-audit record, whatever the outcome. Each record carries a sequence
   number, the previous record's hash and its own SHA-256 hash, so an edit, reorder or insert breaks the chain.
3. At the end of the request the chat API assumes Customer B's audit writer role and writes the request's records
   as one object. The writer can only add objects, and the bucket policy requires create-only writes, Object Lock
   COMPLIANCE and 6 years of retention.
4. Application logs pass the PHI filter: quoted text, emails, SSNs, phone numbers, dates and ids are masked,
   tracebacks lose their messages, paths become route shapes and client IPs are cut to /24 or /48.
5. Server logs never hold SQL, user text, row values or tokens.

## Specification

### Agent audit record

| Field | Content |
|---|---|
| `kind`, `outcome` | Session open, SQL call or escalation grant; `ok`, `blocked`, `error` or `expired` |
| `customer`, `user`, `request_id` | From the verified token; the request id also appears in the STS session name |
| `version`, `escalate` | Gold version read; whether `lossless.*` was mounted, with the stated reason (masked, at most 300 characters) |
| `sql`, `sql_sha256` | The first 500 characters of the SQL, and its SHA-256 |
| `row_count`, `column_count` | Result size. Result cells are never stored |
| `seq`, `prev_hash`, `hash` | The hash chain |

The query audit can hold literal values the user typed, so it is treated as PHI and stored under the customer's
prefix with the query-audit key.

### Security alarms (on the CloudTrail stream)

| Alarm | Fires on |
|---|---|
| Cross-customer denial | `AccessDenied` on S3 or KMS for a reader, parser or transcript role |
| Key change | PutKeyPolicy, DisableKey or ScheduleKeyDeletion on a ClaimClarity key |
| Bucket change | Bucket policy, lifecycle, versioning, Object Lock or encryption changes |
| Break-glass | Any use of the support break-glass role |
| Root use | Any sign-in or call by the root user |

### Retention

| Record | Kept | Enforced by |
|---|---|---|
| Raw EDI files | 7 years | Object Lock COMPLIANCE; deletes denied |
| Query audit | 6 years | Object Lock COMPLIANCE; deletes denied |
| CloudTrail | 6 years | Object Lock on the log bucket |
| Network and application logs | 1 year | CloudWatch Logs retention |
| Transcripts | 90 days | S3 lifecycle; users can delete sooner |

### Failure behaviour

| Case | Result |
|---|---|
| Agent-audit write fails | The SQL result is not returned. The run fails rather than run unaudited SQL |
| Query-audit object already exists | The write is refused. Records are never replaced |
| CloudTrail delivery delayed | AWS retries. Nothing is lost |

## Related

Up: [Network & security](50-network-security.md) · See also: [Encryption keys](52-encryption-keys.md) ·
[Monitoring](62-monitoring.md) · [SQL guard & escalation](44-sql-guard-escalation.md)
