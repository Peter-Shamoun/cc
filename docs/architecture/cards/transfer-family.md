### AWS Transfer Family
| | |
|---|---|
| Purpose in ClaimClarity | The SFTP endpoint where customer labs deliver their daily 837P, 835 and 277 files |
| Talks to | Customer SFTP clients (SSH); Amazon S3 (writes into the customer's `landing/` prefix) |
| Permissions | One user per customer, signed in with the customer's SSH key. The user's home folder maps to `<customer>/landing/` only. A copy step, as the customer's SFTP copy role, then writes each file into `raw/` create-only, with Object Lock and the customer's lake key, and writes `_SUCCESS` last. |
| Encryption | SSH in transit; SSE-KMS with the customer's lake key at rest. The bucket policy refuses a raw write that is not create-only with Object Lock COMPLIANCE retention of at least 2,555 days. |
| Data held / PHI | Passes full PHI through; stores nothing itself |
| Scaling and limits | Managed service; one server for all customers, one user per customer |
| Failure behaviour | A day without the lab's `_SUCCESS` is never copied or parsed. A name that already exists in raw is refused; the lab sends a corrected file under a new name. |
| Logging and audit | Session logs (user, connect, file names) in CloudWatch Logs; CloudTrail records each S3 write and KMS call |
| Used on pages | [SFTP intake](11-sftp-intake.md) · [Ingestion & lake](10-ingestion.md) |
