# ccsim: the ClaimClarity data backend, simulated locally

`ccsim/` runs the backend from `docs/data-backend/design-doc.md` on one machine, without AWS. Code talks
to it the way it will talk to real AWS. Stock **boto3** and **DuckDB httpfs** connect to a local
S3/STS endpoint that verifies real SigV4 signatures. Every request passes the same IAM, bucket-policy and
KMS checks AWS would make, and every decision is logged as a CloudTrail record. The policies in
`infra.py` are plain AWS JSON: `python -m ccsim.infra --export DIR` writes them out for Terraform.

```bash
pip install -e ".[dev]"
pytest                                             # ~2 min, 140+ tests
python -m ccsim.edi_mock /tmp/mock --days 30       # mock 837P/835 + ledger (add --stress for every edge case)
python -m ccsim.edi_mock /tmp/big --mb-per-day 5 --days 365 --tenants acme_labs \
    --each 'ingest {dir} && rm -r {dir}'           # a year at ~5 MB/business day, one day dir at a time
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
| `pipeline.py`, `lake.py` | The Fargate parser. Under a per-customer S3 lock: bronze → silver parts (edi.source_file last, as the commit record), quarantining bad files. Loads are idempotent. Gold is maintained incrementally: only the claims a batch touches are recomputed and only their partitions rewritten (`GOLD_MODE=full` rebuilds everything). A new gold version is a manifest; CURRENT moves to it with a compare-and-set. Closed months are compacted. Layout and measurements: `docs/data-backend/lake-optimization.md`. |
| `query.py` | The per-request session: Cognito token → STS → CURRENT → manifest → DuckDB views over exactly the files of that version, scoped to this customer's gold prefix. |
| `edi_mock.py`, `x12_validate.py`, `parser.py` | Mock EDI and its ledger; an independent validator; the X12 → lossless-schema parser. Patients, places and diagnoses come from `edi_reference.py` (Census/SSA name frequencies, real city/ZIP pairs and area codes per lab state, FY2026 billable ICD-10-CM codes in clinical domains so each line points at a fitting diagnosis, NCCI panel components a claim scrubber strips). A 90-day hidden burn-in (`Scenario.burn_in_days`) makes day 1 steady state: the window's 835s also answer, recoup and rework claims billed before it, and the ledger keeps only what the window delivered. The generator is calibrated to `research/edi_realism_calibration.yaml` (Medicare MAC + MA + Medicaid + Blues + national + a long payer tail, CLFS-based allowed amounts, XiFin denial mix, payer remittance cycles, holidays, seasonality; the CY2026 CLFS ships in `data/clfs_2026q4.csv`; `tests/test_edi_calibration.py` checks the distributions). Each lab is its own market around those typical values (`tenant_market`: payer mix with the state's Medicaid managed-care share, order-profile and test-menu weights; a tail of regional plans and TPAs from the lab's state and its neighbours; COB in NAIC order). It streams day by day with flat memory; volume is set in real units (`Scenario.target_mb_per_day`); the ledger is `truth.json` (index) + `truth/<customer>/claims.jsonl`, read back with `edi_mock.load_truth`. |

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
| Incremental gold equals a full rebuild | Same view SQL over each touched claim's full silver history; a batch that crashed after its silver commit is caught up by the next one. | `test_incremental_gold_equals_full_rebuild` |
| One batch per customer at a time | S3 lease lock (`If-None-Match` create, `If-Match` takeover after expiry). | `test_batch_lock_is_exclusive_and_expires` |

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
7. **Open (semantic layer): an exact-duplicate denial (CO/OA-18 on an accidental resend) that arrives after
   the payment becomes the "current" adjudication** and flips a paid claim to denied (CO-18, or status 4).
   The calibrated generator sends resends on ~1.5% of claims (duplicates are 15% of lab denials), half
   before and half after the original pays. The ledger follows the business rule as written (latest event
   per claim+payer is current), so gold and ledger agree; `has_duplicate_denial` marks the claims where the
   rule probably gives the wrong business answer. Decide whether the view should skip CARC-18 events.
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
