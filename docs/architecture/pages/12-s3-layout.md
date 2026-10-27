# S3 layout

[Overview](00-overview.md) › [Ingestion & lake](10-ingestion.md) › S3 layout

One S3 bucket, the ClaimClarity bucket, holds all customer data, with one prefix per customer. Files move
through the prefix from `landing/` to `raw/`, `parquet/` (silver) and `semantic_layer/` (gold), and each zone
is open only to the role that needs it.

![S3 layout](../png/12-s3-layout.png)

## In this diagram

| Item | What it does | Details |
|---|---|---|
| `landing/` | Holds SFTP uploads until they are copied to raw. | [SFTP intake](11-sftp-intake.md) |
| `raw/` · bronze | Keeps the EDI files as received, write-once, locked 7 years. | [SFTP intake](11-sftp-intake.md) |
| `parquet/` · silver | Holds the lossless tables, by table and received month. | [Parser task](14-parser-task.md) |
| `semantic_layer/` · gold | Holds `CURRENT`, one manifest per version and the gold data files. | [Gold publish](15-gold-publish.md) |
| `staging/` · lease | Holds `LOCK`, the customer's batch lease. | [Parser task](14-parser-task.md) |
| `quarantine/` | Holds copies of files that failed to parse. | [Parser task](14-parser-task.md) |
| `transcripts/` · `audit/` | Hold chat state and the query audit, under their own keys. | [State](33-state.md) |
| `customer_a/` | Every other customer has the same layout with its own roles and keys. | [Customer isolation](23-customer-isolation.md) |
| SFTP copy role | Moves files from `landing/` into `raw/`. | [SFTP intake](11-sftp-intake.md) |
| Parser role | Reads raw and writes the derived zones. | [Parser task](14-parser-task.md) |
| Reader role | Reads gold only. | [Token to credentials](22-token-to-credentials.md) |
| B's lake key | Encrypts every lake object of Customer B. | [Encryption keys](52-encryption-keys.md) |

## How it works

1. The SFTP copy role moves uploads from `landing/` into `raw/`.
2. The parser role reads `raw/`, takes the lease in `staging/`, quarantines bad files, writes `parquet/` and
   publishes `semantic_layer/`.
3. The reader role reads `semantic_layer/` and nothing else.

## Specification

### Keys

```
<customer>/landing/received_date=YYYY-MM-DD/{837,835,277}/<file>
<customer>/raw/received_date=YYYY-MM-DD/{837,835,277}/<file>   + _SUCCESS
<customer>/staging/LOCK
<customer>/quarantine/<run_id>/<file>
<customer>/parquet/<table>/received_month=YYYY-MM/{part,compacted}-<run_id>.parquet
<customer>/semantic_layer/CURRENT
<customer>/semantic_layer/v=<run_id>/manifest.json
<customer>/semantic_layer/<relation>/<partition>/part-<run_id>-<n>.parquet
```

### Permissions by zone

| Zone | SFTP copy role | Parser role | Reader role |
|---|---|---|---|
| `landing/` | Get, Delete | – | – |
| `raw/` | Put, PutObjectRetention | Get, GetObjectVersion | – |
| `staging/` | – | Get, Put, Delete | – |
| `quarantine/` | – | Put, Delete | – |
| `parquet/` | – | Get, Put, Delete | – |
| `semantic_layer/` | – | Get, Put, Delete | Get, List |

### Zones, PHI and retention

| Zone | PHI | Retention |
|---|---|---|
| `landing/` | Yes | Deleted after the copy |
| `raw/` | Yes | Object Lock COMPLIANCE 2,555 days; no deletes; no lifecycle rule |
| `staging/` | No | Old versions expire after 1 day |
| `quarantine/` | Yes | Kept for review |
| `parquet/` | Yes | Old versions expire after 7 days |
| `semantic_layer/` | No direct patient identifiers | Retired files deleted after 20 minutes; old versions expire after 1 day |

### Bucket settings

| Setting | Value |
|---|---|
| Versioning, Object Lock | On. Retention is set per raw object, not as a bucket default. |
| Encryption | SSE-KMS. Writes must name the writer's own lake key; the bucket default key is refused for customer prefixes. |
| Bucket policy | One document for all customers: TLS only; principals without a `customer` tag denied; access only under `${aws:PrincipalTag/customer}/`; raw write-once and never deleted |
| Who may change bucket settings | Only the Terraform deploy role |
| Other buckets | The web app and the access logs have their own buckets |
| Logging | CloudTrail S3 data events for every object call |

## Related

Up: [Ingestion & lake](10-ingestion.md) · See also: [IAM and KMS matrix](91-iam-kms-matrix.md)
