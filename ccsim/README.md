# ccsim: the data backend, run locally

`ccsim/` runs the backend from `docs/data-backend/design-doc.md` on one machine, with no AWS account. Each
AWS service is a small in-process stand-in that makes the same allow/deny decisions AWS would. The
policies in `infra.py` are the ones to translate into Terraform, and the S3 calls use boto3's method
names, so moving to real AWS means swapping the client factory.

```bash
pip install -e ".[dev]"
pytest tests/test_ccsim.py                         # build a 25-day world, check accuracy and every boundary
python -m ccsim.edi_mock /tmp/mock --days 30       # write mock 837P/835 files and truth.json
python bench/eval.py --agent gold                  # check the benchmark harness itself
python bench/eval.py --agent claude --judge        # baseline text-to-SQL agent (needs ANTHROPIC_API_KEY)
python bench/perf.py --days 100                    # storage-layout benchmark
```

## Simulated service → real resource

| Design-doc step | ccsim | Terraform / prod |
|---|---|---|
| Per-customer folder, own key | `create_key` per customer; `put_object` refuses any key except the prefix's own key; `get_object` checks `kms:Decrypt` against the key policy | `aws_kms_key` + key policy per customer; bucket policy requires SSE-KMS with that key |
| Only that customer's roles may touch the folder | Bucket policy `Deny` + `NotPrincipal` per prefix. Explicit Deny wins over `s3:*` (tested with a broad admin role) | `aws_s3_bucket_policy` |
| Raw is immutable | Object Lock globs on `<c>/raw/*`: no overwrite, no delete | Object Lock (compliance mode) on raw/ |
| SFTP → S3 copy | `infra.sftp_drop`: `cc-sftp-copy-<c>` role, `PutObject` on raw/ only, then `_SUCCESS` | IT's existing script, scoped role |
| `_SUCCESS` → EventBridge → Fargate | S3 emits "Object Created"; a rule per customer filters on the `<c>/raw/` prefix and the `_SUCCESS` suffix, then calls `run_task` | `aws_cloudwatch_event_rule` + ECS target |
| Fargate task scoped to one customer, ended afterwards | `run_task` assumes `cc-parser-<c>`, runs `pipeline.parse_batch`, then revokes the credentials | `aws_ecs_task_definition` with that task role |
| Staging → silver, quarantine, gold rebuild | `parse_batch`: parse each file into `staging/<run>/`, promote to `parquet/<table>/received_month=YYYY-MM/part-<run>.parquet`, quarantine bad files, rebuild every `sem.*` view into `semantic_layer/v=<run>/`, flip `CURRENT`, keep one previous version | same code in the container |
| Entra ID → Cognito → STS | `IdentityPlane`: an Entra tenant + group gate, then a Cognito user→customer map, then a signed token with an expiry; `credentials_for` exchanges the token for the customer's read role (15-minute credentials) | Entra OIDC federation into a Cognito user pool, then `AssumeRoleWithWebIdentity` |
| DuckDB per request | `query.open_session`: fresh in-memory DuckDB. `allowed_directories` is set to this customer's current gold version, then `enable_external_access=false` and `lock_configuration=true` | httpfs plus `CREATE SECRET ... SCOPE 's3://…/<c>/'` |
| CloudTrail | `aws.audit_log()`: every Allow and Deny, with the principal and the session name | CloudTrail data events on the bucket |

**What the local stand-in does not do.** Bodies on disk are not encrypted: KMS is checked on every
access, but the bytes are stored in clear. Policy evaluation covers Allow/Deny with Action, Resource,
Principal and NotPrincipal. It has no Conditions, so rules like `aws:SecureTransport` and
`s3:x-amz-server-side-encryption-aws-kms-key-id` are hand-coded checks in `put_object`. To test the real
wire protocol, the next step is running the same flows against moto server or LocalStack.

## Mock EDI (`edi_mock.py`)

The generator writes two tenants (`acme_labs`, `acme_diagnostics`), six invented payers (commercial,
Medicare Advantage, Medicaid), 13 lab CPTs with their own denial rates and denial CARCs, and invented
patients and providers. Every NPI passes the Luhn check. Files arrive like a clearinghouse feed: one 837P
batch per business day, plus one 835 per payer per payment day. Payers take 5–45 days to adjudicate,
depending on the payer.

It also writes the edge cases that break naive SQL:
- $0 paid with CO-45, which is not a denial
- the two denial definitions (recorded vs signal v3)
- a CO-16 denial followed by a frequency-7 resubmission
- a reversal followed by a correction, and a reversal with no correction
- SVC06 procedure changes and missing REF*6R
- PLB recoupments and interest
- byte-identical 835 re-deliveries under a new filename
- claims that are never remitted

`truth.json` is the per-claim ledger. The generator computes it from its own events, without the parser
or the SQL views, so it can catch bugs in the parser and in the views.

All knobs are on `Scenario` (`days`, `volume_scale`, and each scenario probability). A mock-data agent
can write new `Scenario`s, or new payer/CPT tables, to stress a particular behaviour.

## Benchmarks (`bench/`)

- `eval.py` + `questions.yaml`: 16 questions, from easy counts to the hard cases (reversals, the
  `$0 ≠ denial` trap, CPT rates with a minimum group size) plus one PHI refusal. Each question runs the
  candidate SQL and the gold SQL, then compares the result sets (order-insensitive, rounded). With
  `--judge`, an LLM judge rules on mismatches, using the question, both SQL queries and both results
  (the OpenAI data-agent method). Separately, every gold query with a `truth` key is checked against the
  ledger. Agents are pluggable (`--agent module:Class`), so each new agent design gets a score and a
  latency.
- `perf.py`: storage-layout benchmark (results below).

## Two options for chat transcripts (not built)

- **A: stateless (current assumption).** Nothing is stored after the stream ends. The audit log keeps
  who queried which customer and when, with no prompt or result text. This is the smallest PHI footprint.
  The cost is no history, no eval data from real use, and no "share this analysis".
- **B: stored per customer.** Transcripts and artifacts are written as JSON to
  `s3://…/<customer>/chats/<user>/<conversation>.json`. They use the same customer KMS key and are written
  only by the app role. Users can read only their own prefix, enforced by a session tag on the STS
  credentials. A lifecycle rule expires them after N days. Since results can contain PHI-adjacent claim
  keys, B puts transcripts inside the same boundary as the data. To build B here: add a `chats/` grant to
  `cc-reader-<c>` with an `aws:PrincipalTag/user` condition (that needs Conditions in the evaluator), plus
  a lifecycle stand-in.
