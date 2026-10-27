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
