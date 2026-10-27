# Ingestion & lake

[Overview](00-overview.md) › Ingestion & lake

Each customer sends its daily 837P, 835 and 277 files over SFTP. They land, unchanged and locked, in that
customer's raw prefix in the ClaimClarity bucket. A short-lived parser task then turns one day's files into
lossless tables (silver) and publishes a new version of the semantic tables (gold) that the chat reads. Every
customer has its own prefix, KMS key, roles, EventBridge rule and task definition, so one customer's batch can
never touch another customer's data.

![Ingestion & lake](../png/10-ingestion.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Customer B lab | The customer's SFTP client. Uploads one day's files, then an empty `_SUCCESS` marker. | [SFTP intake](11-sftp-intake.md) |
| 2 | AWS Transfer Family | SFTP endpoint. One user per customer, whose home folder is that customer's `raw/`. | [SFTP intake](11-sftp-intake.md) |
| 3 | Raw (bronze) | The files exactly as received, one folder per received day. Write-once, under Object Lock for 7 years. | [S3 layout](12-s3-layout.md) |
| 4 | EventBridge | Customer B's rule fires when its `_SUCCESS` marker lands and starts the parser task. | [Parse trigger](13-parse-trigger.md) |
| 5 | Parser task | Amazon ECS on AWS Fargate, as Customer B's parser role. Reads the day's raw files, writes silver, publishes gold, exits. | [Parser task](14-parser-task.md) |
| 6 | Silver | Lossless Parquet tables, one part file per table per batch. | [S3 layout](12-s3-layout.md) |
| 7 | Gold | `sem.*` tables and PHI-stripped copies of the lossless tables. One version per batch. | [Gold publish](15-gold-publish.md) |
| 8 | Run workers | The chat read path. Reads gold only, as Customer B's reader role. | [Chat turn](32-chat-turn.md) |

## How it works

1. The customer lab uploads a day's files over SFTP (1) into `raw/received_date=YYYY-MM-DD/837/`, `835/` and
   `277/`, and writes `_SUCCESS` last.
2. Transfer Family (2) stores each file in Customer B's `raw/` (3), encrypted with Customer B's key and locked
   for 7 years.
3. The `_SUCCESS` object raises an S3 Object Created event. Customer B's EventBridge rule (4) matches it
   (gate 1) and starts one Fargate task from Customer B's task definition (gate 2).
4. The parser task (5) takes Customer B's batch lease, reads the day folder, skips files it has already loaded,
   and parses the rest. A file that fails to parse is copied to `quarantine/`; the others load.
5. It writes one silver part file per table (6). `edi.source_file` goes last: it is the commit record.
6. It recomputes only the claims the batch touched and writes a new gold version (7). A pointer object,
   `CURRENT`, moves to it with a compare-and-set. Every S3 call in steps 4–6 needs IAM and KMS to allow
   Customer B's parser role (gate 3).
7. The task releases the lease and exits. Its container and disk are destroyed.
8. Chat run workers (8) read `CURRENT`, then the files of that version, as Customer B's reader role (gate 6).

## Specification

### Roles and what they may do

| Role | Allowed | Not allowed |
|---|---|---|
| Customer B's SFTP role | Add new objects (and set their retention) under Customer B's `raw/` | Read, overwrite or delete anything |
| Launcher role (EventBridge target) | Start the parser task definitions in the ClaimClarity cluster; pass the parser roles to ECS tasks | Read or write any data |
| Customer B's parser role | List Customer B's prefix; read `raw/`; read and write `staging/`, `quarantine/`, `parquet/`, `semantic_layer/` | Write or delete `raw/`; touch another customer's prefix |
| Customer B's reader role | List and read Customer B's `semantic_layer/` | Read raw or silver; write anything |

Each role carries two tags: `customer` and `kms_key_arn`. The bucket policy uses them, so the same policy
covers any number of customers. Full matrix: [IAM and KMS matrix](91-iam-kms-matrix.md).

### Data zones

| Zone | Prefix | PHI | Written by | Kept |
|---|---|---|---|---|
| Raw (bronze) | `raw/` | Yes | SFTP role | Object Lock COMPLIANCE, 2,555 days (7 years); never deleted |
| Silver | `parquet/` | Yes | Parser | Until replaced by compaction; old versions expire after 7 days |
| Gold | `semantic_layer/` | No direct patient identifiers | Parser | Retired files kept 20 minutes, then deleted; old versions expire after 1 day |
| Staging, quarantine | `staging/`, `quarantine/` | Quarantine: yes | Parser | Staging old versions expire after 1 day |

### Encryption

Every object uses SSE-KMS with the customer's own key. The bucket policy refuses a write that names any other
key. The key policy allows Decrypt and GenerateDataKey only to that customer's SFTP, parser and reader roles,
only through S3, and only for objects under that customer's prefix. Disabling the key makes all of that
customer's objects unreadable at once. Details: [Encryption keys](52-encryption-keys.md).

### Failure behaviour

| Event | What happens |
|---|---|
| A file fails to parse | It is recorded as `REJECTED` in `edi.source_file`, copied to `quarantine/<run_id>/`, and the rest of the batch loads |
| The same `_SUCCESS` event arrives twice | The second task finds every file already in `edi.source_file` and exits with status `noop` |
| The task crashes before the silver commit | Nothing becomes visible. The next batch deletes the orphan part files. Re-run the day to load it. |
| The task crashes after the silver commit | Gold is behind silver. The next batch for that customer catches gold up. |
| Two tasks for one customer | The batch lease lets only one run. The other waits up to 15 minutes, then fails. |

### Logging

CloudTrail records every S3, KMS, ECS and IAM call, allowed or denied. Each batch writes an `edi.ingest_run`
row and one `edi.source_file` row per file. The task writes a run summary (counts and timings, no PHI) to
CloudWatch Logs. See [Audit logging](53-audit-logging.md).

## Pages in this area

- [SFTP intake](11-sftp-intake.md): Transfer Family, the SFTP role and the raw write rules
- [S3 layout](12-s3-layout.md): the prefixes of one customer and who may use each
- [Parse trigger](13-parse-trigger.md): EventBridge, the launcher and RunTask (gates 1 and 2)
- [Parser task](14-parser-task.md): the five steps of one batch, the lease, silver commit and compaction (gate 3)
- [Gold publish](15-gold-publish.md): manifests, `CURRENT` and how old versions stay readable

## Related

Up: [Overview](00-overview.md) · See also: [Customer isolation](23-customer-isolation.md) ·
[Encryption keys](52-encryption-keys.md) · [Audit logging](53-audit-logging.md)
