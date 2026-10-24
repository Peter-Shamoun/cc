# CC data backend

Your own write-up of this design is [`design-doc.md`](design-doc.md). This file is a board-by-board companion to the canvas: [`canvas.html`](canvas.html) (published at https://claude.ai/artifact/Q4XqDXcMvkXBbatExDweK4). Its source is `diagrams/data-backend-canvas/`. Each section below matches one board, and the six numbered AWS gates on the diagram are explained in their own table.

## Overview

Customer EDI files (837P, 835, and later 277) land in one PHI-approved prod S3 bucket, with one folder per customer. Two paths use that bucket:

- **Write path:** a short-lived parser per customer batch turns raw EDI into lossless Parquet tables (silver) and the agent-facing semantic layer (gold).
- **Read path:** a logged-in Xifin user's question runs against that customer's gold tables only, with credentials that can't reach any other customer.

Isolation is enforced by AWS (IAM, KMS, STS, bucket policy), not by application code. Every step is shown for customer B; each customer gets the same setup.

## 00 · Storage & isolation

The bucket has a `cc/` prefix with one folder per customer. Each customer folder has its own SSE-KMS key and IAM role:

- the key policy trusts only that customer's role;
- the role can read and write only that customer's prefix;
- a bucket policy denies any role outside its own prefix.

Reading PHI needs both the role's access and the key's decrypt; either one alone gets nothing. CloudTrail data events and KMS decrypt logs record every access.

Each customer folder holds:

| Folder | Layer | Contents |
|---|---|---|
| `raw/received_date=YYYY-MM-DD/` | Bronze | EDI exactly as received (`837/`, `835/`, `277/`) plus a `_SUCCESS` manifest. Never edited. |
| `parquet/` | Silver | The lossless tables (`claude/schema/01_lossless_edi_schema.sql`), one folder per table, partitioned by month |
| `semantic_layer/` | Gold | Every `sem.*` view materialized as Parquet, under `v=<run_id>/`, plus a `CURRENT` pointer to the live version |
| `staging/<run_id>/` | — | In-progress load; deleted if the load fails |
| `quarantine/` | — | Files that failed to parse |

Parquet files are never edited. Each batch adds new files (about one per table per day). There is no compaction job for now; if one is added later, it compacts within a partition and never touches bronze. The full tree is in `s3-folder-layout.txt`.

## 01 · Ingestion & parse

1. On a daily or weekly schedule, the copy script (LabCorp's existing IT script) copies new SFTP files into `raw/`. It writes `_SUCCESS` last, listing every file and its sha256.
2. The `_SUCCESS` write fires an EventBridge rule. The rule works out the customer from the path.
3. A launcher calls ECS RunTask to start a Fargate task as that customer's role. The launcher holds only `iam:PassRole` and can't read data itself.
4. Fargate task steps:
   - It starts with temporary credentials for that customer's role, issued and rotated by ECS.
   - For each file in the manifest, it skips the file if that sha256 is already loaded; otherwise it parses the EDI into `staging/<run_id>/`.
   - Once every file is parsed, it promotes the batch into `parquet/` in one step, so readers never see half a load. If any file fails, that file goes to `quarantine/` and the staging folder is deleted.
   - Once per batch (not per file), it rebuilds `semantic_layer/`. It runs the view SQL from GitHub (`semantic/views/`, placeholder path), writes `v=<run_id>/`, then switches `CURRENT` to it.
   - It exits. The credentials expire and the container and its disk are destroyed.

One task never holds two customers' roles. Customer A runs the same chain in a separate task as role A.

**The view SQL vs the gold layer:** the SQL in GitHub is code only, and only the parser runs it. The gold layer is the Parquet that SQL produces. The read side never runs the view SQL; it reads the materialized tables.

## 02 · Login & customer mapping

The user logs in through Microsoft Entra ID (company login). Cognito turns that login into the app's signed token, and the token names the user's customers (e.g. `customers: [customer_b]`).

The customer comes from Entra group membership, for example `CC-LABCORP → labcorp`. Admins grant access by adding users to groups in Entra; there is no separate user table to keep in sync.

## 03 · Query path

1. The API server (EC2) verifies the token and takes the customer from it. It never takes the customer from the URL or request body; a request like `GET /claims?customer=customer_a` is rejected.
2. It asks STS for 15-minute credentials for that customer's role, with SourceIdentity set to the user so CloudTrail shows who asked.
3. It opens a fresh in-memory DuckDB connection with an S3 secret scoped to `customer_b/`, registers the `sem.*` views over `semantic_layer/<CURRENT>/`, and locks the configuration so agent SQL can't change settings (`duckdb_per_request_connection.py`).
4. The Claude agent (Bedrock, under a BAA) asks for metrics by name. The metric compiler writes the SQL over the semantic-layer tables. The guarded escape-hatch SQL is also limited to `sem.*`.
5. The connection closes after the request, and the credentials expire within 15 minutes.

If agent SQL ever pointed at `customer_a/`, IAM would deny it: role B's credentials can't read customer A's data, whatever the app code does.

## The six AWS gates

| # | Gate | What it checks | Enforced by |
|---|---|---|---|
| 1 | Rule matches B? | The EventBridge rule fires only for a `_SUCCESS` under `customer_b/` and starts customer B's task, so one customer's upload can't start another customer's parser. | EventBridge rule pattern |
| 2 | PassRole allowed? | The launcher may hand the Fargate task role B, and nothing else. It can't use the role itself. | IAM `iam:PassRole` condition |
| 3 | IAM + KMS allow? (write) | Every S3 read and write by the parser needs role B's prefix permission and decrypt/encrypt on key B. | IAM policy, KMS key policy, bucket policy |
| 4 | Token valid? | The API accepts only a valid, unexpired token signed by Cognito, and the customer comes from that token. | Cognito JWT verification |
| 5 | Trust policy allows B? | Role B's trust policy lets only the API's role assume it, with the user's SourceIdentity recorded. | STS / IAM trust policy |
| 6 | IAM + KMS allow? (read) | DuckDB's reads with the temporary credentials succeed only on `customer_b/`, and only with key B. | IAM policy, KMS key policy, bucket policy |

## Also decided

- Claude is reached through Bedrock under a BAA. Invocation logging is off, or kept inside the PHI boundary.
- Audit logs: CloudTrail S3 data events and KMS logs go to a separate Object-Lock log bucket and are queried with Athena.

## Not settled yet

- Read-side compute: the canvas shows DuckDB built into an EC2 API server. ADR-010 (central DuckDB on EC2) and ADR-011 (Fargate with DuckDB built in) disagree with it and with each other.
- Lane 2: guarded agent SQL over PHI-stripped silver views is designed but not built. Today the agent sees only `semantic_layer/`.
- The real GitHub path for the view SQL. `semantic/views/` is a placeholder.
- How file supersession (`duplicate_of_file_id`, `superseded_by_file_id`) works when Parquet is append-only: either rewrite `edi.source_file` each batch, or add a separate log table.
- Chat transcripts and an app-level audit log, and retention periods (audit logs about 6 years, transcripts 30–90 days). These need compliance sign-off.
- 277 files arrive later. A possible later move to Iceberg / S3 Tables.
