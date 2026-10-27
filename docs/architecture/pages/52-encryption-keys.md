# Encryption & keys

[Overview](00-overview.md) › [Network & security](50-network-security.md) › Encryption & keys

Every object in the ClaimClarity bucket is encrypted with SSE-KMS, and the key depends on whose data it is and what
kind of data it is. Each customer has a lake key and a separate transcript key. The query audit has its own key.
A key policy is the final word in KMS, so even an IAM administrator with full permissions cannot decrypt a customer's
objects or rewrite the policies that protect them.

![Encryption & keys](../png/52-encryption-keys.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | B's lake roles | Parser, reader and SFTP copy roles for Customer B. Tagged `customer = B` and with B's key | [Parser task](14-parser-task.md) |
| 2 | Raw · silver · gold | `B/raw/`, `B/parquet/`, `B/semantic_layer/` (and staging, quarantine) | [S3 layout](12-s3-layout.md) |
| 3 | Customer B's key | The lake key. Decrypts only for B's lake roles, only through S3, only under `B/` | |
| 4 | B's transcript roles | Transcript user, customer admin and eval roles for Customer B | [State](33-state.md) |
| 5 | Transcripts | `B/transcripts/`: per-user chat transcripts, metadata and de-identified exports | [State](33-state.md) |
| 6 | B's transcript key | A second per-customer key, for transcripts only. The lake roles cannot use it | |
| 7 | Audit roles | Customer B's audit writer (assumed by the chat API) and audit reader (customer admins) | [Audit & logging](53-audit-logging.md) |
| 8 | Query audit | `B/audit/queries/`: one write-once record per request, kept 6 years | [Audit & logging](53-audit-logging.md) |
| 9 | Query-audit key | One platform key with one statement per customer | |

## How it works

1. Every request to the bucket must use TLS. The bucket policy denies any request where `aws:SecureTransport` is
   false, for every principal.
2. Every role that writes carries a `kms_key_arn` tag. The bucket policy denies a `PutObject` whose SSE-KMS key id
   is not that tag's value. A PUT without the header is denied too. So each object is encrypted with its writer's
   key, never with the bucket default.
3. S3 asks KMS for a data key per object. S3 Bucket Keys are off, so each object's encryption context is its own
   object ARN, and the key policy can check it.
4. On every read, S3 asks KMS to decrypt that object's data key. The key policy checks the role, that the call comes
   through S3 (`kms:ViaService`), and that the object ARN is under the customer's prefix.
5. Transcripts get a second layer. Before a message body reaches S3, the chat API encrypts it with a per-user data
   key from the transcript key, bound to the encryption context `customer`, `purpose = transcript-dek` and `user`.
   Then S3 encrypts the object again with SSE-KMS under the same transcript key.
6. Disabling a customer's key makes every object under it unreadable at once, for every role (crypto-shredding).
   Disabling the transcript key removes the chats and leaves the lake and the query audit readable.

## Specification

### Keys

| Key | One per | Encrypts | Rotation | Notes |
|---|---|---|---|---|
| Lake key | Customer | Raw, silver, gold, staging, quarantine under the customer's prefix | Automatic yearly; on-demand by key admins | Only the customer's parser, reader and SFTP copy roles |
| Transcript key | Customer | `transcripts/` objects and the per-user message data keys | Automatic yearly | Lake roles are not principals on it |
| Query-audit key | Platform | `<customer>/audit/queries/` for every customer | Automatic yearly | One key-policy statement per customer, so A's writer cannot decrypt B's records |
| Platform default key | Platform | Bucket default only | Automatic yearly | The bucket policy makes it unusable for customer objects |

### Who may decrypt what

| Role | Lake key (own customer) | Transcript key (own customer) | Query-audit key |
|---|---|---|---|
| Parser | Decrypt + GenerateDataKey via S3, `<customer>/*` | no | no |
| Reader (chat) | Decrypt + GenerateDataKey via S3, `<customer>/*`. Its IAM policy only reads gold, so in practice it decrypts gold objects | no | no |
| SFTP copy | Decrypt + GenerateDataKey via S3, `<customer>/*`. Its IAM policy only writes raw | no | no |
| Transcript user | no | Own folder via S3; own data key only (`user` = own `sub`) | no |
| Customer admin (transcripts) | no | All users' folders, exports and admin objects via S3; any user data key of the customer | no |
| Eval | no | Decrypt via S3, `transcripts/exports/` only | no |
| Support break-glass | no | Decrypt only, users' folders and data keys, one customer per session, ticket required | no |
| Audit writer | no | no | Decrypt + GenerateDataKey via S3, own customer's `audit/queries/` |
| Audit reader | no | no | Decrypt via S3, own customer's `audit/queries/` |
| Chat API | no | Through the transcript roles only | Through the audit writer only |
| Key admin | no data use | no data use | no data use |
| Deploy (Terraform) | no data use | no data use | no data use |

A customer's roles never appear on another customer's keys.

### Key administration

| Who | May | May not |
|---|---|---|
| Deploy role (Terraform) | PutKeyPolicy, GetKeyPolicy, DescribeKey, tags, enable rotation, schedule or cancel deletion | Decrypt, encrypt, generate data keys, create grants |
| Key admin role | Describe, list, enable, disable, rotation settings, rotate on demand, schedule or cancel deletion | Decrypt, encrypt, create grants, PutKeyPolicy |
| IAM administrators | nothing on these keys: the key policies do not delegate to the account | |

The deploy role is the trusted path. Protect it with an SCP and CloudTrail alarms on PutKeyPolicy and on bucket
policy changes. Key deletion has a 7–30 day waiting period, and a deletion can be cancelled during it.

### Bucket policy: encryption and transport

| Statement | Denies |
|---|---|
| DenyInsecureTransport | Every request without TLS |
| PutsUseOwnCustomerKey | A PUT whose SSE-KMS key id is not the caller's `kms_key_arn` tag |
| DenyPrincipalsWithoutACustomer | Any principal without a `customer` tag, except the deploy role |
| ObjectsOnlyUnderOwnCustomer | Object access outside `${aws:PrincipalTag/customer}/` |
| OnlyTerraformChangesBucketConfig | Changes to bucket policy, lifecycle, versioning, Object Lock or encryption by anyone but the deploy role |

### Other encryption

| Data | Encryption |
|---|---|
| SQS run messages | SSE with a ClaimClarity KMS key |
| CloudWatch Logs | Log groups encrypted with a ClaimClarity KMS key |
| Secrets Manager | A ClaimClarity KMS key |
| In transit | TLS on every hop. See [Network](51-network.md) |

### Failure behaviour

| Case | Result |
|---|---|
| Key disabled | Every read and write under it fails with a KMS error (`KMS.DisabledException`). Re-enabling restores access |
| Key pending deletion | Same as disabled until the deletion is cancelled. After the waiting period the data is gone for good |
| Wrong key on PUT | Denied by the bucket policy |
| Wrong encryption context | KMS refuses. A data key from one object cannot decrypt another |

### Logging

Every Decrypt and GenerateDataKey call is a CloudTrail event with the role, the key and the encryption context
(the object ARN). Key policy changes, disables and deletions are management events with alarms. See
[Audit & logging](53-audit-logging.md).

## Related

Up: [Network & security](50-network-security.md) · See also: [Customer isolation](23-customer-isolation.md) ·
[S3 layout](12-s3-layout.md) · [IAM and KMS matrix](91-iam-kms-matrix.md) · [KMS card](90-service-catalog.md)
