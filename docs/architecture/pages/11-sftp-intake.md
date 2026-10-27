# SFTP intake

[Overview](00-overview.md) › [Ingestion & lake](10-ingestion.md) › SFTP intake

Customers deliver their EDI files over SFTP to AWS Transfer Family. Each customer is one Transfer Family user
whose home folder is that customer's `raw/` prefix in the ClaimClarity bucket, so a customer can only ever add
files to its own folder. Raw files are the source of truth: they are encrypted with the customer's key, locked
for 7 years and can never be changed or deleted.

![SFTP intake](../png/11-sftp-intake.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Customer B sender | The customer's SFTP client, signed in with the customer's SSH key | |
| 2 | AWS Transfer Family | SFTP server. Maps the user's home folder to Customer B's `raw/`. Writes to S3 as Customer B's SFTP role. | [Transfer Family card](90-service-catalog.md) |
| 3 | Customer B's raw/ | One folder per received day, with `837/`, `835/`, `277/` and the `_SUCCESS` marker | [S3 layout](12-s3-layout.md) |
| 4 | EventBridge | Starts the parser when `_SUCCESS` lands | [Parse trigger](13-parse-trigger.md) |
| 5 | Customer B's SFTP role | The only identity that writes raw files for Customer B | |
| 6 | Customer B's key | KMS key that encrypts every object under Customer B's prefix | [Encryption keys](52-encryption-keys.md) |
| 7 | Bucket policy | Refuses any raw write that is not create-only, under the owner's key and locked for 7 years | [Customer isolation](23-customer-isolation.md) |
| 8 | CloudTrail | Records every write and key use, allowed or denied | [Audit logging](53-audit-logging.md) |

## How it works

1. The sender (1) opens an SFTP session (SSH, port 22) to Transfer Family (2) with Customer B's SSH key.
2. It uploads one received day into `received_date=YYYY-MM-DD/837/`, `835/` and `277/`.
3. Transfer Family writes each file to Customer B's `raw/` (3) as Customer B's SFTP role (5). Each object is
   encrypted with Customer B's key (6). The bucket policy (7) checks every write.
4. When every file of the day is uploaded, the sender writes an empty `_SUCCESS` object into the same day
   folder. That object is the signal that the day is complete.
5. S3 sends an Object Created event for `_SUCCESS` to EventBridge (4), which starts the parser.
6. CloudTrail (8) records each PutObject and each KMS call.

## Specification

### Folder and file names

| Item | Value |
|---|---|
| Day folder | `<customer>/raw/received_date=YYYY-MM-DD/` |
| File folders | `837/`, `835/`, `277/` under the day folder |
| Completion marker | `_SUCCESS`, empty, written last |
| Overwrites | Not possible. A corrected file is sent under a new name; both are kept. |
| Re-sent identical bytes | Kept. The parser marks the later copy as a duplicate of the first. |

### Permissions

| Who | May | May not |
|---|---|---|
| Customer B's Transfer Family user | Upload to its home folder (Customer B's `raw/`) | See any other folder |
| Customer B's SFTP role | `s3:PutObject` and `s3:PutObjectRetention` on Customer B's `raw/` | Read, list, overwrite or delete; write any other prefix |
| Customer B's key | Encrypt for the SFTP, parser and reader roles of Customer B, through S3 only | Be used by any other principal, or outside S3 |

### Raw write rules (bucket policy)

The bucket policy refuses a write to any `raw/` prefix unless all of these hold:

| Rule | Value |
|---|---|
| Transport | TLS only |
| Principal | Carries a `customer` tag, and the key is under that customer's prefix |
| Encryption | SSE-KMS with the key named in the principal's `kms_key_arn` tag |
| Create-only | The write must be conditional on the key not existing (`If-None-Match`) |
| Retention | Object Lock mode COMPLIANCE, at least 2,555 days (7 years) remaining |
| Deletes | Denied for everyone, on objects and versions |

The bucket has versioning and Object Lock enabled. Retention is set per object, not as a bucket default,
because a bucket default would also lock silver, gold and staging. No lifecycle rule applies to `raw/`.

### Data held

Raw EDI holds full PHI: patient names, member ids, birth dates, addresses and the payer's bank details.

### Limits and failure behaviour

| Event | What happens |
|---|---|
| A write without the right key, retention or create-only condition | The bucket policy denies it; CloudTrail records the denial |
| A file name that already exists | Denied (create-only). The sender must use a new name. |
| `_SUCCESS` never written | No parse starts for that day. The files stay in raw until the marker arrives. |
| Files added after `_SUCCESS` | `_SUCCESS` cannot be written twice, so they load only when operations re-run that day's parse |

### Logging

CloudTrail S3 data events (every PutObject, allowed or denied), KMS calls, and Transfer Family session logs
(user, connect and disconnect, file names) in CloudWatch Logs. Logs carry file names, never file contents.

## Related

Up: [Ingestion & lake](10-ingestion.md) · Next: [Parse trigger](13-parse-trigger.md) · See also:
[S3 layout](12-s3-layout.md) · [Encryption keys](52-encryption-keys.md)
