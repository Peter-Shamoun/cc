# ccsim: the CC data backend, simulated locally

`ccsim/` runs the backend from `docs/data-backend/design-doc.md` on one machine, without AWS. Code talks
to it the way it will talk to real AWS. Stock **boto3** and **DuckDB httpfs** connect to a local
S3/STS endpoint that verifies real SigV4 signatures. Every request passes the same IAM, bucket-policy and
KMS checks AWS would make, and every decision is logged as a CloudTrail record. The policies in
`infra.py` are plain AWS JSON: `python -m ccsim.infra --export DIR` writes them out for Terraform.

```bash
pip install -e ".[dev]"
pytest                                             # ~2 min, 140+ tests
python -m ccsim.edi_mock /tmp/mock --days 30       # mock 837P/835 + truth.json (add --stress for every edge case)
python -m ccsim.x12_validate /tmp/mock/*/raw/*/*/*.x12
python -m ccsim.infra --export /tmp/policies       # policy JSON for Terraform
python -m bench.eval selftest                      # benchmark datasets and scoring (see bench/README.md)
```

```python
from ccsim.world import build_world
from ccsim.query import login, open_session
w = build_world("/tmp/cc", latency_ms=20)          # provision, generate, deliver day by day through the pipeline
s = open_session(w.aws, w.dep, login(w.aws, "ana@xifin.example"))
s.con.execute("SELECT payer_name, avg(is_denied::INT) FROM sem.remit_claims WHERE is_current_adjudication GROUP BY 1")
```

## Layout

| Module | What it simulates |
|---|---|
| `aws/iam.py` | Policy language and evaluation. Covers explicit deny, identity/resource/session policies, permissions boundaries, KMS key-policy authority with root delegation, all condition operators (`IfExists`, `ForAnyValue`/`ForAllValues`), policy variables, exact-match principals, and write-time validation. |
| `aws/kms.py` | Customer keys and key policies. Envelope encryption is real AES-256-GCM, with the encryption context bound as AAD. Covers disable/enable (crypto-shredding), rotation, scheduled deletion and grants. |
| `aws/s3.py` | Versioning and Object Lock (COMPLIANCE/GOVERNANCE retention, legal hold), conditional writes (`If-None-Match`, `If-Match`) and SSE-KMS. Also S3's condition keys, ranges, ListObjectsV2 (delimiter, pagination, UTF-8 order), lifecycle, EventBridge notifications, and authorized bucket-policy changes with the 20 KB limit. Bytes on disk are ciphertext. |
| `aws/sts.py` | AssumeRole, AssumeRoleWithWebIdentity with session tags, duration limits, role chaining, transitive tags, and session revocation the console's way. |
| `aws/identity.py` | Entra ID (gate 1: tenant and app-assignment groups) federated into a Cognito user pool (gate 2: the user must be mapped to one customer). Tokens are real RS256 JWTs. |
| `aws/events.py`, `aws/ecs.py` | EventBridge content filtering, retries, dead-letter queue and at-least-once duplicates. ECS RunTask authorized with `ecs:RunTask` plus `iam:PassRole`; tasks get task-role credentials. |
| `aws/http.py` | The SigV4 endpoint: S3 REST (path style) and the STS Query API. Supports presigned URLs and aws-chunked bodies, plus injectable latency and bandwidth. |
| `aws/cloudtrail.py` | One CloudTrail-shaped record per API call, allowed or denied. |
| `infra.py` | The account as Terraform would build it: roles, ABAC bucket policy, key policies, lifecycle, the EventBridge rule and task definitions. |
| `pipeline.py` | The Fargate parser. Bronze → staging → silver, quarantining bad files. Loads are idempotent. Gold is rebuilt from silver, CURRENT is flipped with a compare-and-set, and old versions are cleaned up. |
| `query.py` | The per-request session: Cognito token → STS → DuckDB scoped to this customer's current gold version. |
| `edi_mock.py`, `x12_validate.py`, `parser.py` | Mock EDI and its ledger; an independent validator; the X12 → lossless-schema parser. |

## Security model, and the test that checks each piece

| Guarantee | How | Test |
|---|---|---|
| A user sees only their customer | Entra assignment, then the Cognito `custom:customer` attribute. The reader role's trust policy requires `aws:RequestTag/customer` = that customer, and the tag comes from the signed token. | `test_token_for_one_customer_cannot_assume_another_customers_role` |
| Agent SQL cannot reach another customer | Four independent layers: IAM role, ABAC bucket policy, DuckDB secret scoped to this customer's current gold version, `allowed_directories`. | `test_layer_*`, one per layer, each with the other layers switched off |
| The reader never touches PHI | The reader role covers gold only. For escalation, PHI-stripped copies of lossless tables are materialized into gold. | `test_gold_has_no_direct_patient_identifiers`, `test_reader_is_read_only_and_gold_only` |
| An IAM admin with `*:*` can't read customer data or change its guards | The bucket policy denies untagged principals, and only Terraform may change bucket config. Key policies give data use to the customer's roles via S3 only, and give admins no `CreateGrant` or `PutKeyPolicy`. | `test_admin_with_star_permissions_still_cannot_read_phi` |
| Raw is immutable | Versioning plus Object Lock COMPLIANCE for at least 7 years, `If-None-Match` required, deletes denied. | `test_raw_is_write_once_and_undeletable` |
| A customer can be offboarded instantly | Disabling the customer's key leaves its objects unreadable everywhere. | `test_customer_offboarding_by_disabling_its_key` |
| Duplicate events don't double-load | The parser skips raw objects already recorded in silver. | `test_redelivered_success_event_is_a_noop` |
| Readers never see a half-built or older gold | CURRENT is flipped by a conditional write, and only forward. | `test_current_never_moves_backwards` |

## What the simulation found (fix before or while porting)

1. **Fixed: `sem.payments` and `sem.provider_adjustments` double-counted re-delivered 835s.** They now skip
   files flagged `duplicate_of_file_id`, in `claude/schema/02_semantic_layer_views.sql`.
2. **Fixed: the parser role needed `s3:GetObjectVersion`** to read raw by version id.
3. **Fixed in `infra.py` after review: the earlier per-customer bucket policy hit S3's 20 KB limit at about
   17 customers.** The ABAC version is the same size for any number of customers.
4. **Fixed: the key policy let account-root delegates call `CreateGrant` / `PutKeyPolicy`.** An IAM admin
   could have granted themselves Decrypt.
5. **Open (semantic layer): `sem_int.submission` ranks the latest submission per claim across payers.**
   For a claim billed to a secondary payer, `sem.claims` with `is_latest_submission` therefore shows the
   secondary payer. Benchmark questions avoid the trap through `payer_responsibility = 'P'`; the view
   should probably rank per claim and payer.
6. **Open (semantic layer): `payer_sequence` comes from CLP02, so it is `unknown` for denials and
   reversals.** Filtering `payer_sequence = 'primary'` silently drops denied claims (benchmark q12 tests
   this trap).
7. **Open (semantic layer): an exact-duplicate denial (CO-18 on an accidental resend) becomes the
   "current" adjudication** and flips a paid claim to denied. Only `Scenario.stress()` generates it; the
   ledger keeps the business answer, so the self-test shows the disagreement on stress data.
8. **Open (DuckDB): inside its allowed directory, a session can run `COPY ... TO 's3://…'` and
   `CREATE SECRET`.** Only the read-only IAM role and the SQL guard (`bench/guard.py`) stop these, so keep
   both.
9. **Open (ECS): task-role credentials outlive the task.** AWS does not revoke them at stop; they expire
   on their own schedule, up to about 6 h. The PHI boundary is the destroyed container, not the
   credentials.

## Check in real AWS before relying on these

- **Session tags from Cognito:** the claim `https://aws.amazon.com/tags` in Cognito id tokens needs a
  pre-token-generation trigger (V2 event, which may need a paid feature plan) that can emit an
  object-valued claim. The reader trust policy depends on it.
- **Multipart uploads:** IT's copy script switches to multipart above 8 MB. The local endpoint rejects
  multipart (501), so multipart PUTs to raw/ and the SSE key-id condition on UploadPart are untested.
  Exercise both in a dev bucket.
- **Bucket default retention:** setting bucket-level default retention (COMPLIANCE, 2555 days) would also
  lock gold and staging, so the design sets retention per object. That makes it the writer's job, which the
  bucket policy enforces.
- **ECS:** prod also needs a task execution role (ECR pulls, logs) that the EventBridge role may pass, and
  a network configuration on RunTask.
- **Trusted path:** the Terraform role can change everything. Protect it with an SCP, MFA on the human
  path, and CloudTrail alerts.
- **Not simulated:** IAM Organizations/SCPs, S3 Access Points, VPC endpoints and their policies, Macie,
  GuardDuty, real TLS.
