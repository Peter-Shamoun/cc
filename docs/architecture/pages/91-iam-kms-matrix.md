# IAM and KMS matrix

[Overview](00-overview.md) › IAM and KMS matrix

Every role in ClaimClarity: which prefixes of the ClaimClarity bucket it may read or write, which key it may use, and
who may assume it. `<c>` is one customer; each customer has its own copy of the per-customer roles and keys.

Two rules hold for every row:
- The bucket policy denies object access outside `${aws:PrincipalTag/customer}/`.
- Key policies are authoritative and allow use only through S3, for objects under the customer's own prefix.

| Role | May read | May write | Key it may use | Who may assume it (trust) |
|---|---|---|---|---|
| **Per customer** | | | | |
| Parser | List `<c>/`; `raw/`, `staging/`, `parquet/`, `semantic_layer/` | `staging/`, `parquet/`, `semantic_layer/`, `quarantine/` (put and delete) | `<c>`'s lake key: Decrypt, GenerateDataKey | ECS tasks in the ClaimClarity account, passed only by the launcher role |
| Reader | List and get `semantic_layer/` (current versions only) | nothing | `<c>`'s lake key: Decrypt, GenerateDataKey (used to read gold) | Cognito ID token for the ClaimClarity app client with `customer = <c>` (AssumeRoleWithWebIdentity) |
| SFTP copy | nothing | `raw/` (create only, with retention) | `<c>`'s lake key: Decrypt, GenerateDataKey (used to write raw) | The SFTP intake copy step |
| Transcript user | Own folder `transcripts/users/<sub>/` | Own folder (put and delete) | `<c>`'s transcript key: own folder and own data key | Cognito ID token with `customer = <c>` |
| Transcript admin | All of `transcripts/` | Delete in `transcripts/users/`; put and delete in `transcripts/exports/` and `transcripts/admin/` | `<c>`'s transcript key: users, exports, admin; any user data key of `<c>` | Cognito ID token with `customer = <c>` and `transcript_admin = true` |
| Eval | List and get `transcripts/exports/` | nothing | `<c>`'s transcript key: Decrypt, exports only | The eval team role |
| Audit writer | nothing | `audit/queries/` (create only, with retention) | Query-audit key: `<c>`'s statement | The chat API role, with session tags `customer = <c>` and `user` |
| Audit reader | List and get `audit/queries/` | nothing | Query-audit key: Decrypt, `<c>`'s statement | Cognito ID token with `customer = <c>` and `transcript_admin = true` |
| **Platform** | | | | |
| Chat API | nothing | nothing | none | ECS tasks (chat API service). It may assume only the audit writer roles |
| Run worker task role | nothing in the bucket | nothing | none | ECS tasks (run worker service). It reads data only through a customer's reader role |
| Launcher (EventBridge target) | nothing | nothing | none | EventBridge in the ClaimClarity account. May run the parser task definitions and pass the parser roles to ECS tasks |
| Support | nothing | nothing | none | Support staff through company SSO. May assume only the break-glass role |
| Support break-glass | `<c>/transcripts/users/` for the customer named in its session tag | nothing | `<c>`'s transcript key: Decrypt only | The support role, with the external id, a `TICKET-*` reason tag and customer and operator tags |
| Eval team | nothing | nothing | none | Eval staff through company SSO. May assume the per-customer eval roles |
| Key admin | nothing | nothing | All keys: enable, disable, rotate, schedule deletion. No decrypt, no grants, no policy changes | Key administrators through company SSO |
| Deploy (Terraform) | nothing | Bucket configuration only (policy, lifecycle, versioning, Object Lock, encryption) | All keys: key policy and lifecycle. No data use | The deploy pipeline |

All prefixes are under `<c>/` in the ClaimClarity bucket. Every per-customer role carries the tags `customer = <c>`
and `kms_key_arn`. The bucket policy denies a PUT that names any key other than the one in the writer's
`kms_key_arn` tag.

## Related

See also: [Identity & access](20-identity.md) · [Customer isolation](23-customer-isolation.md) ·
[Encryption keys](52-encryption-keys.md) · [S3 layout](12-s3-layout.md)
