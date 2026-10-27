# Parser task

[Overview](00-overview.md) › [Ingestion & lake](10-ingestion.md) › Parser task

The parser task turns one customer's received day into silver and gold. It runs on Amazon ECS on AWS Fargate,
as that customer's parser role, and lives for one batch. All parsing happens inside the container: no data
leaves it except as encrypted objects in the customer's own prefix. A lease makes sure only one batch per
customer runs at a time, and a commit record makes every batch all-or-nothing.

![Parser task](../png/14-parser-task.png)

## In this diagram

| # | Item | What it does | Details |
|---|---|---|---|
| 1 | Start | Takes Customer B's batch lease and removes leftovers of crashed batches | Lease, below |
| 2 | Parse | Lists the day folder, skips files already loaded, parses the rest, quarantines bad files | Bad files, below |
| 3 | Silver | Writes one part file per table, then the commit record | Silver commit, below |
| 4 | Gold | Builds a new gold version, moves `CURRENT`, then compacts silver | [Gold publish](15-gold-publish.md) |
| 5 | Exit | Releases the lease and logs the run summary. The container is destroyed. | |
| 6 | `staging/` · `quarantine/` | The lease object; copies of rejected files | [S3 layout](12-s3-layout.md) |
| 7 | `raw/` | Read only, by object version | [S3 layout](12-s3-layout.md) |
| 8 | `parquet/` (silver) | Lossless part files and compacted month files | [S3 layout](12-s3-layout.md) |
| 9 | `semantic_layer/` (gold) | Data files, manifests and `CURRENT` | [Gold publish](15-gold-publish.md) |
| 10 | CloudWatch Logs | The run summary: counts, run id, phase timings. No PHI. | [Monitoring](62-monitoring.md) |

**Gate 3** applies to every arrow on this page: each S3 call needs IAM to allow Customer B's parser role on
that prefix, and KMS to allow Customer B's key for that role, through S3, for an object under Customer B's
prefix.

## How it works

1. **Start.** The task reads `CUSTOMER`, `BUCKET`, `KMS_KEY_ARN` and `SUCCESS_KEY` from its environment. It takes
   the lease `staging/LOCK` (6). It lists silver once and deletes garbage: part files of runs that never wrote
   their commit record, and files a newer compacted file covers.
2. **Parse.** It lists the day folder in `raw/` (7) and reads each object's version id. A file whose key and
   version are already in `edi.source_file` is skipped. The rest are read by version id and parsed. Rows are
   staged in an in-memory DuckDB database under the lossless schema, spilling to the task's disk above 256 MB.
   A file that fails to parse gets a `REJECTED` row and is copied to `quarantine/<run_id>/` (6).
3. **Silver.** It renews the lease, then writes one Parquet part file per table to
   `parquet/<table>/received_month=YYYY-MM/part-<run_id>.parquet` (8). It writes `edi.source_file` last. That
   file is the batch's commit record.
4. **Gold.** It renews the lease and builds a new gold version (9), then moves `CURRENT` to it (see
   [Gold publish](15-gold-publish.md)). After that it compacts silver months.
5. **Exit.** It releases the lease, writes the run summary to CloudWatch Logs (10) and exits. Fargate destroys
   the container and its disk.

## Specification

### Task

| Item | Value |
|---|---|
| Launch | Amazon ECS RunTask, Fargate launch type, one task definition per customer |
| Task role | Customer B's parser role, maximum session 1 hour |
| Size | 4–8 GB of memory for 5 MB of raw EDI per day; `PARSE_WORKERS` set to the task's vCPUs |
| Storage | Fargate ephemeral storage, encrypted, gone when the task stops. Temporary files live in a temporary directory only. |
| Engine | DuckDB with `httpfs`, extensions not auto-installed or auto-loaded; S3 through boto3 with task-role credentials |
| Settings | `GOLD_MODE` (`incremental` default, or `full`), `PARSE_WORKERS` (default 1), `SILVER_OPEN_MONTH_MAX_PARTS` (default 4) |
| Duration | Seconds to a few minutes per batch at 5 MB/day; the lease allows up to 2 hours |

### Permissions (Customer B's parser role)

| Action | Resource |
|---|---|
| `s3:ListBucket` | The ClaimClarity bucket, prefix `customer_b/` only |
| `s3:GetObject`, `s3:GetObjectVersion` | `raw/`, `staging/`, `parquet/`, `semantic_layer/` under Customer B |
| `s3:PutObject`, `s3:DeleteObject` | `staging/`, `parquet/`, `semantic_layer/`, `quarantine/` under Customer B |
| `kms:Decrypt`, `kms:GenerateDataKey` | Customer B's key, only through S3, only for objects under `customer_b/` (granted in the key policy) |

The role cannot write or delete `raw/`, cannot read another prefix and cannot use another key. The bucket
policy denies all of that again, independently.

### Batch lease and fencing

| Item | Value |
|---|---|
| Object | `<customer>/staging/LOCK`, a small JSON body: holder (task ARN), expiry, random nonce |
| Take | Create with `If-None-Match: *`. If it exists and has expired, take it over with `If-Match` on its ETag. |
| Wait | Up to 15 minutes, checking every second, then the task fails |
| Lease length | 2 hours, longer than any batch. A crashed holder's lease expires after that. |
| Renew | Rewritten with `If-Match` on the holder's own ETag before each write that other batches depend on: the silver commit record, compaction and `CURRENT` |
| Fencing | If the renew fails, someone else has taken the lease. The task stops with `LockLost` before that write, so a task that overran its lease can never commit. |
| Release | Overwritten with an expired lease, again with `If-Match`. A task never removes another holder's lease. |

### Silver commit

- A batch writes its part files directly into silver. There is no copy step.
- `edi.source_file` is written last. A run with that part is committed; a run without it is garbage.
- The next batch deletes garbage while it holds the lease, so a crash never leaves rows visible twice.
- Loads are idempotent. Keys are deterministic (`<file_id>:<segment number>`), and files already recorded by key
  and version are skipped.
- A byte-identical re-delivery under another name is loaded and marked with `duplicate_of_file_id`; the gold
  views skip it for payments.

### Compaction

| Partition | When | Result |
|---|---|---|
| A closed month (older than the newest month in silver) | Once, after gold includes every committed run | One `compacted-<run_id>.parquet` per table, sorted by file and segment order |
| The open month, for tables gold reads | When a table has 4 daily parts beside its compacted file | The same |

The compacted file is written first; the files it covers are deleted after. A crash in between leaves a state the
next batch resolves: the newest compacted file wins and the rest is garbage. Gold compaction is described in
[Gold publish](15-gold-publish.md).

### Bad files and retries

| Event | What happens |
|---|---|
| Broken envelope: no ISA, missing SE/GE/IEA, control numbers that do not match, a segment outside a transaction set, an unsupported transaction set | The file is rejected: a `REJECTED` row with the message (up to 500 characters) in `edi.source_file`, a copy in `quarantine/<run_id>/`. The other files load. The raw original stays. |
| Wrong SE01 segment count | Loaded and flagged (`count_check_ok = false`), since the content is complete |
| Any other error | A bug. The batch fails before its commit record; nothing becomes visible. Fix and re-run the day. |
| Lease busy for 15 minutes | The task fails without writing. Re-run the day. |
| Lease lost mid-batch | `LockLost`; the task stops before the next shared write |
| Crash after the silver commit | Gold is behind. The next batch for that customer includes the missing runs. |
| No new files (duplicate event) | Status `noop`, unless gold is behind, in which case gold is caught up |

A quarantined file is fixed by the customer sending a corrected file under a new name.

### Data held

The task holds full PHI in memory and on its ephemeral disk while it runs. It writes PHI only to Customer B's
`parquet/` and `quarantine/`. Gold gets no direct patient identifiers (see [Gold publish](15-gold-publish.md)).

### Logging

| Record | Where | Contents |
|---|---|---|
| `edi.ingest_run` | Silver | Run id, start and end, parser version, files seen, loaded and rejected, task ARN, event id |
| `edi.source_file` | Silver | One row per file: S3 key, version id, ETag, size, SHA-256, KMS key, parse status and messages |
| Run summary | CloudWatch Logs | Status (`ok` or `noop`), counts, phase timings, the gold build report. No PHI. |
| API calls | CloudTrail | Every S3 and KMS call, allowed or denied |

## Related

Up: [Ingestion & lake](10-ingestion.md) · Previous: [Parse trigger](13-parse-trigger.md) · Next:
[Gold publish](15-gold-publish.md) · See also: [S3 layout](12-s3-layout.md)
