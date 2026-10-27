### AWS Transfer Family
| | |
|---|---|
| Purpose in ClaimClarity | The SFTP endpoint where customer labs deliver their daily 837P, 835 and 277 files |
| Talks to | Customer SFTP clients (SSH); Amazon S3 (writes into the customer's `raw/` prefix) |
| Permissions | One user per customer, signed in with the customer's SSH key. The user's home folder maps to `<customer>/raw/` only. Transfer Family writes as that customer's SFTP role: `s3:PutObject` and `s3:PutObjectRetention` on that `raw/` prefix only; no read, list or delete. |
| Encryption | SSH in transit; SSE-KMS with the customer's key at rest. Every raw write must be create-only and carry Object Lock COMPLIANCE retention of at least 2,555 days, or the bucket policy refuses it. |
| Data held / PHI | Passes full PHI through; stores nothing itself |
| Scaling and limits | Managed service; one server for all customers, one user per customer |
| Failure behaviour | A refused write fails the upload for that file; the sender retries with a new name. A day without `_SUCCESS` is never parsed. |
| Logging and audit | Session logs (user, connect, file names) in CloudWatch Logs; CloudTrail records each S3 write and KMS call |
| Used on pages | [SFTP intake](11-sftp-intake.md) · [Ingestion & lake](10-ingestion.md) |
