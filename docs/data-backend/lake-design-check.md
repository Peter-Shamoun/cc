# Lake design check: hand-built medallion vs DuckLake vs Iceberg

The question: was it right to build commits, versions, compaction, retention and the batch lock by hand (gold
manifests, CURRENT compare-and-set, cohort/quarter partitions, the `staging/LOCK` lease), instead of using a table
format that does these natively? This note compares the current lake against two table formats on the same
workload and the same security model:

- **(a) DuckLake** (DuckDB 2.0, ducklake extension, format 1.1);
- **(b) Apache Iceberg** (pyiceberg 0.9.1 with a SQL catalog, read by DuckDB's iceberg extension), with S3 Tables
  assessed on paper;
- **(c) the current lake** (`ccsim/lake.py`, `ccsim/pipeline.py`, `ccsim/query.py`, unchanged).

**Recommendation: keep the current design.** DuckLake is the one serious alternative. It does less work per batch:
about 4x fewer bytes written and less than half the CPU. It commits all gold tables atomically and needs about
half the code. But it moves the PHI-bearing metadata (column min/max statistics and, by default, inlined rows) out
of the customer's KMS-encrypted prefix into a catalog. Only a per-customer, per-layer catalog file kept in S3
preserves the security model. That setup brings back a hand-rolled compare-and-set commit, and the catalog file
adds 3.7 MB to every agent request. Without daily compaction, DuckLake's queries cost 4-5x as many requests as
ours. Iceberg through pyiceberg 0.9.1 was the most expensive of the three and the least mature. Its upside (wide
engine support, and S3 Tables' managed maintenance) doesn't apply to a single-engine, DuckDB-only lake at
5 MB/day/customer. The details, and what would change this recommendation, follow.

## 1. Method

- **Workload:** 120 business days of `acme_labs` at 5 MB of raw EDI per day, from 2026-01-05 (627 MB raw), made
  with `ccsim.edi_mock` (generator defaults, 90-day burn-in). Every day was parsed once with the unchanged
  `ccsim.parser` (`bench/design_check/gen_days.py`), so all three designs ingest identical rows.
- **Same gold logic:** the prototypes reuse the unchanged `02_semantic_layer_views.sql` and `pipeline.py`'s
  `CLOSURE`, `_view_columns`, `_working_catalog`, `ESCALATION` and the payers merge
  (`bench/design_check/gold_flow.py`). The incremental idea is the same: recompute the touched claims from their
  full silver history, then replace them in gold.
  - DuckLake does `DELETE` + `INSERT` in one transaction across all 25 gold tables.
  - Iceberg does `overwrite(overwrite_filter=In(claim_key, touched))` per table.
- **Same account:** the unchanged `ccsim.infra` (IAM roles, ABAC bucket policy, per-customer KMS key, write-once
  raw). Silver lives under `<c>/parquet/`, gold under `<c>/semantic_layer/`. The parser and reader roles and their
  policies are the production ones.
- **Two bench-only additions to the local endpoint**, monkey-patched in `bench/design_check/simenv.py` (nothing in
  `ccsim/aws` changed):
  - multipart upload, which pyarrow uses. Parts are stored through the normal PutObject path, so the bucket policy
    and KMS still decide;
  - accounting of the endpoint's own CPU, so client CPU = process CPU − endpoint CPU.
- **No wall-clock.** Every number is a request count, bytes from the endpoint's counters, or CPU seconds. For the
  current pipeline, the parse CPU of the same day (measured once) is subtracted, so every row is lake maintenance
  only.
- **Agent workload:** the 29 `gold_sql` queries of `bench/questions.yaml`, each in a fresh reader session (STS web
  identity → catalog or CURRENT → DuckDB views; `allowed_directories` and `lock_configuration` as in `query.py`).
  - Session open and query are measured separately.
  - Each result is fingerprinted. **All 29 results of DuckLake and Iceberg equal the current lake's at every
    checkpoint (20, 60 and 120 days; Iceberg 20 and 60)**, before and after compaction.
- **Maintenance policy:**
  - Current: as built (incremental gold, monthly silver compaction and quarter retiering, 20-minute retention).
  - DuckLake: at month close, `merge_adjacent_files` for silver, plus `rewrite_data_files(delete_threshold => 0)`
    and `merge_adjacent_files` for gold. At every batch start, `expire_snapshots` and `cleanup_old_files`; the
    previous batch is a day old, longer than any reader session.
  - Iceberg: month-close compaction as a full per-partition or per-table overwrite. pyiceberg 0.9.1 has no
    `rewrite_data_files` and no `expire_snapshots`, so nothing is ever deleted.
  - Both formats are also measured "after compacting now" at each checkpoint, as the lower bound for a daily
    compaction job.
- **Iceberg ran 60 of the 120 batches.** It was already the most expensive design per batch. Without snapshot
  expiry its bucket grew to 3.9 GB at 60 days, against 0.6 GB for the current lake, and the full run would have
  passed the 6 GB disk budget. Its 21-60 figures are what it costs before costs grow further.

Prototype code is in `bench/design_check/` (about 1,000 lines with the harness). Raw outputs are in
`bench/design_check/results/`. Replay with:

```
python -m bench.design_check.gen_days DATA 120
python -m bench.design_check.run {current|ducklake|iceberg} DATA OUT.json   # iceberg: a venv with pyiceberg==0.9.1
python -m bench.design_check.summarize current.json ducklake.json iceberg.json
python -m bench.design_check.security_probe DATA WORLD
```

## 2. Measured comparison

### Per batch (median of ordinary batches; one customer, 5 MB raw/day)

| | current (c) | DuckLake (a) | Iceberg (b) |
|---|---|---|---|
| requests, days 1-20 / 21-60 / 61-120 | 483 / 802 / 1,181 | 575 / 864 / 1,190 | 1,423 / 1,573 / not run |
| MB read, same windows | 36 / 137 / 293 | 51 / 131 / 240 (36 of it is the catalog file, see note) | 53 / 171 / – |
| MB written, same windows | 16 / 58 / 125 | 26 / 29 / 29 | 23 / 67 / – |
| client CPU s (lake work only), same windows | 7.4 / 16.5 / 29.7 | 7.5 / 10.5 / 13.0 | 16.1 / 26.6 / – |
| last 5 ordinary batches: requests / MB read / MB written / CPU s | 1,491 / 372 / 160 / 39.5 (days 116-120) | 1,551 / 299 / 31 / 13.9 (116-120) | 2,179 / 259 / 94 / 34.3 (56-60) |
| month-close batch (incl. compaction) at 60 / 120 days, requests / MB read / MB written / CPU s | 2,335 / 248 / 210 / 39 ; 3,152 / 685 / 538 / 94 | 2,586 / 422 / 350 / 69 ; 3,274 / 868 / 689 / 98 | 8,459 / 618 / 415 / 92 (at 39 days) |
| catalog commits per batch | 1 CURRENT CAS + 1 manifest | 2 catalog-file CAS (silver, gold); 1 gold snapshot for all 25 tables | 50 (25 silver + 25 gold table commits) |
| totals over the run | 120 batches: 121,383 req, 24.9 GB read, 11.4 GB written, 2,874 CPU s | 120 batches: 124,036 req, 22.4 GB read, 5.6 GB written, 1,683 CPU s | 60 batches: 103,892 req, 8.9 GB read, 3.8 GB written, 1,528 CPU s |

Note: the DuckLake prototype keeps its catalog as a DuckDB file in S3, for the security reasons in section 3.
Every batch downloads and re-uploads both catalog files, about 9 MB each: 4 requests and ~36 MB. A Postgres
catalog would replace that transfer with SQL round trips. Excluding it, DuckLake reads ~204 MB per batch at
61-120 days, against 293 MB for the current lake.

What the numbers say:

- **DuckLake writes 4x less and uses about half the CPU.** Its deletes are merge-on-read: a touched claim's old
  rows get a small deletion file. The current lake rewrites the touched cohort partitions copy-on-write, and those
  rewrites grow with the straggler quarter. DuckLake pays this back at query time and at month close (see the
  agent workload table).
- **Requests are a wash between (a) and (c)** (~1,200 per batch at 61-120 days). Both are dominated by the
  closure lookup over silver. In DuckLake they grow with the gold files and deletion files that build up within a
  month: in February, 340 requests on the day after compaction and 1,165 just before the next month close; in
  March, 462 and 1,476.
- **Iceberg through pyiceberg costs the most per batch.** Its 50 table commits are each 4-5 objects: a data file,
  a manifest, a manifest list, `metadata.json`, plus reads of the previous metadata. Its copy-on-write deletes
  rewrite whole data files, and its CPU is the highest of the three.

### Agent workload (29 `gold_sql` queries, a fresh session each)

| | session open: requests / MB | query requests, median / max | query MB, median / max | client CPU ms, median (open+query) |
|---|---|---|---|---|
| current, 60 days | 22 / 0.06 | 12 / 49 | 1.53 / 8.67 | 204 |
| current, 120 days | 22 / 0.06 | 14 / 43 | 3.40 / 15.77 | 189 |
| DuckLake, 60 days, mid-month (as maintained) | 11 / 1.85 | 73 / 147 | 7.26 / 21.95 | 518 |
| DuckLake, 60 days, just compacted | 11 / 1.85 | 12 / 25 | 2.93 / 8.15 | 438 |
| DuckLake, 120 days, mid-month | 11 / 1.85 | 59 / 130 | 6.71 / 22.81 | 554 |
| DuckLake, 120 days, just compacted | 11 / 1.85 | 11 / 29 | 3.28 / 14.09 | 423 |
| Iceberg, 60 days, as maintained | 21 / 0.88 | 29 / 60 | 4.47 / 18.53 | 574 |
| Iceberg, 60 days, just compacted | 21 / 0.89 | 6 / 23 | 0.63 / 6.45 | 520 |

- **Session open:**
  - Ours: STS, CURRENT, manifest, then 10 view binds on zero-row schema files (60 KB in all).
  - DuckLake: attaches its catalog file over httpfs, which reads 1.85 MB at open and ~3.7 MB per request
    including the query's catalog lookups (DuckDB-file blocks are 256 KB). That is the DuckDB-file catalog's cost;
    Postgres would be a few round trips instead.
  - Iceberg: one `metadata.json` per relation. Each one holds the table's whole snapshot history, so open grew
    from 0.30 MB at 20 days to 0.88 MB at 60.
- **Mid-month, both formats cost 2-5x more requests per query** than the current lake. The cause is small daily
  files plus deletion files (DuckLake) or copy-on-write leftovers (Iceberg). Freshly compacted, they match or beat
  ours: Iceberg needs 6 requests and 0.63 MB. Keeping them there needs daily compaction, roughly the
  month-close cost (DuckLake about 2,000 requests, 500-760 MB read, 450-750 MB written, 73-87 CPU s at
  60-120 days). That would undo most of their per-batch write saving. The current lake gets its read layout
  (one file per cohort/quarter, sorted, 1M-row groups) as part of every batch.
- **Reader CPU is 2-3x ours in both formats** (catalog attach and metadata parsing). It is small in absolute terms
  (0.4-0.6 s).

### Storage (live bytes; "stored" includes noncurrent versions until lifecycle expires them)

| | silver live MB | gold live MB | stored MB, all versions |
|---|---|---|---|
| current, 60 / 120 days | 315 / 616 | 300 / 605 | 615 / 1,221 |
| DuckLake, 60 / 120 days, mid-month | 344 / 670 | 266 / 500 | 663 / 1,224 |
| Iceberg, 60 days (no expiry possible in pyiceberg 0.9.1) | 867 | 3,036 | 3,903 |

- **DuckLake matches the current lake in size** once its cleanup runs. Its compaction leaves a full second copy
  until the next batch's `cleanup_old_files` (1,972 MB right after compacting at 120 days).
- **Iceberg without expiry keeps every copy-on-write rewrite.** Gold is 10x the current lake's at 60 days and
  grows faster than linearly. pyiceberg 0.10+ adds `expire_snapshots`; S3 Tables runs expiry as a managed service.

## 3. Security fit

The probes are in `bench/design_check/security_probe.py` and their output in
`bench/design_check/results/security-probe.json`. Both ran on the unchanged infra.

| check | current (c) | DuckLake (a) | Iceberg (b), pyiceberg + Glue/SQL catalog | Iceberg on S3 Tables (paper) |
|---|---|---|---|---|
| per-customer KMS key on every object | yes | yes: DuckDB secret `KMS_KEY_ID`. A write without it is refused by the bucket policy (probed) | **not with stock pyiceberg 0.9.1**: neither FileIO can set an SSE-KMS key, so the bucket policy refuses its writes (probed). Needs a custom FileIO (`KmsS3FileIO`, ~40 lines); pyiceberg 0.12 adds `s3.sse-kms-key-id` for fsspec only | SSE-KMS per table bucket or table with a customer-managed key (verify per-table key support and limits) |
| prefix-scoped IAM roles, ABAC bucket policy | yes | yes, unchanged, as long as each catalog is a file under the customer's prefix | yes for data and metadata files, unchanged; the catalog (Glue) needs its own IAM statements per database | **no**: S3 Tables uses `s3tables:*` actions on table-bucket/table ARNs, not object prefixes. The ABAC bucket policy and the six-gate model would have to be rewritten for a second service |
| where the catalog lives | none: CURRENT + manifest under `semantic_layer/` | DuckDB file per layer in S3 (prototype), or Postgres/SQLite (intended) | Glue / SQL / REST catalog: pointers only. Stats live in manifests under the table's prefix | AWS-managed REST catalog per table bucket |
| PHI in metadata | manifests list keys only; Parquet footers stay under the key | **silver catalog holds PHI**: column min/max for patient names, first names, DOB, address and member id (probed). **Data inlining is on by default**: small inserts are stored as rows in the catalog (probed: 8 rows of `edi.entity`). Set the limit to 0 and give silver its own catalog | silver manifests hold the same PHI bounds (probed), under `<c>/parquet/`, encrypted with the customer key, out of the reader's reach | same bounds in managed manifests, under the table's KMS key |
| reader limited to gold | yes: reader policy is `semantic_layer/*`, plus per-relation `allowed_directories` | yes with **two catalogs per customer**: the reader can attach gold and is denied the silver catalog and silver files (probed). One shared catalog would show the reader silver's PHI stats | yes: gold scans are allowed, silver metadata is denied (probed) | per-table / namespace grants (`s3tables:GetTableData`); Lake Formation for finer grants |
| non-escalated session kept off `lossless.*` | yes, per-relation secret and directory | yes: per-table data directories in `allowed_directories`. The reader still sees lossless tables' names and stats in the gold catalog (PHI-stripped) | yes, per-table location | via grants |
| write-once raw untouched | yes | yes: writes only under `parquet/` and `semantic_layer/` (probed) | yes (probed) | yes (raw stays in the general-purpose bucket) |
| new trust / PHI surface | none | the catalog: with Postgres, a database whose stats and inlined rows are PHI, encrypted with one RDS key, guarded by DB grants instead of IAM+KMS; with `ENCRYPTED` Parquet, it also holds the file keys | the catalog holds pointers and table properties only; small | a second AWS service in the PHI boundary (needs BAA-eligibility confirmation), with its own policy language |
| tenant isolation | IAM + bucket policy + KMS + DuckDB scope | the same with catalog files; with Postgres, per-customer databases or schemas plus per-customer DB roles (RDS IAM auth could map them to the reader role) | IAM + KMS as today, plus Glue resource policies per customer database | one table bucket (or namespace) per customer; default table-bucket quota is small (verify; per-account limits) |
| crypto-shredding a customer | disable or delete the key: nothing of theirs decrypts | works for files and S3-resident catalogs (probed: catalog file, silver data and Iceberg metadata all fail with `KMS.DisabledException`). **A Postgres catalog survives the key**: its PHI stats and inlined rows must be dropped separately | works: metadata is in S3 under the key; Glue keeps only pointers | works if every table uses the customer key; the managed catalog keeps names and pointers |

Two security findings decide the DuckLake question:

1. **The catalog is PHI.** DuckLake's whole value is its SQL catalog. To keep "role + key, or nothing" for silver,
   that catalog has to be per customer, per layer and encrypted with the customer key. The only form that fits the
   current IAM/KMS model without new infrastructure is a catalog file in S3. A catalog file in S3 means a
   download, an attach and a compare-and-set upload: the same hand-built commit pattern as CURRENT, only with a
   9 MB object instead of a 20-byte one.
2. **Readers range-read a mutable catalog object.** Iceberg readers pin an immutable `metadata.json`. A DuckLake
   reader of a catalog file reads blocks lazily while the writer may replace the object, which risks a torn read.
   It needs `s3_version_id_pinning` (new in DuckDB 2.0) or immutable catalog keys behind a pointer, which again is
   the current design.

## 4. Correctness and robustness

| | current (c) | DuckLake (a) | Iceberg (b), pyiceberg 0.9.1 |
|---|---|---|---|
| batch atomicity across gold relations | one CURRENT CAS publishes all 25 relations | **native**: one transaction, one snapshot for all gold tables | **none**: 25 independent table commits. A crash mid-gold leaves readers seeing `sem.claims` with batch N and `sem.remit_claims` without it. The prototype records `cc.silver_through` per table, so a re-run is idempotent, but the inconsistency window is real. Multi-table commits need a REST catalog that implements them (not Glue, not S3 Tables, not pyiceberg 0.9.1) |
| silver commit | `edi.source_file` part last = commit record; garbage swept under the lock | one transaction for all silver tables; then the catalog-file CAS | 25 table commits, `edi.source_file` last (the same commit-record trick as ours) |
| concurrency | single writer per customer: `BatchLock` lease with fencing, plus the CURRENT CAS | catalog-file CAS (optimistic; the loser's files become orphans for `delete_orphaned_files`). Postgres: optimistic with retries, conflicts per table | optimistic per table; the catalog CAS retries |
| crash recovery | tested: crash after silver commit, mid-compaction, lost lock | orphan files from an unpublished commit; gold catches up from silver's change feed (`ducklake_table_insertions`), as long as silver snapshots after gold's mark are not expired (the prototype keeps them) | per-table re-run by property; orphans need `remove_orphan_files` (not in pyiceberg 0.9.1) |
| snapshot isolation for readers | manifest + 20-minute retention of retired files | snapshot per attach; expiry must stay older than the reader session lifetime. The same rule as ours, now a parameter | pinned `metadata.json`; files survive until expiry |
| schema evolution | rules hash triggers a full gold rebuild; silver is read with `union_by_name` | `ALTER TABLE ADD/RENAME` is metadata-only | field-id-based evolution, the strongest of the three |
| time travel for audits | silver is append-only, so "as of run X" is `ingest_run_id <= X`. Gold as of a date = rebuild from silver with the view SQL of that date. Gold older than 20 minutes is gone | any retained snapshot; retention costs a full copy per compaction | any retained snapshot; same storage cost |
| incremental gold | hand-built (closure + cohort partitions + retiering + full-rebuild triggers) | the same closure, but no cohort partitions, no retiering and no "late batch means full rebuild", because claims are replaced by key | same as DuckLake |
| hand-built lake code | ~590 lines (`lake.py` + gold half of `pipeline.py`), several review-found bugs fixed (see `lake-optimization.md` §6) | ~240-line prototype + 76 shared | ~330-line prototype + 76 shared |

**Maturity in 2026, as found in this check:**

- **DuckLake** worked first time on DuckDB 2.0. It is DuckDB-only in practice. Two behaviours had to be found by
  hand: default inlining puts rows in the catalog, and the default `rewrite_data_files` threshold left every
  deletion file in place until it was set to 0. A month-close compaction was OOM-killed once on the shared
  machine without a `memory_limit`.
- **pyiceberg 0.9.1** needed five workarounds for this workload:
  - no SSE-KMS;
  - decimals written by pyarrow 26 rejected (pyiceberg pins pyarrow < 20);
  - all-NULL decimal statistics crash the writer;
  - DuckDB's nullable results rejected against required silver fields on overwrite;
  - no compaction, expiry or orphan removal, and no incremental scan or multi-table transaction.

  Several of these are fixed in 0.10-0.12; compaction is still not in pyiceberg.
- **DuckDB's iceberg extension** read everything correctly. Its writes target REST catalogs.

## 5. Operational comparison (production on AWS)

| | current (c) | DuckLake (a) | Iceberg (b) self-managed (Glue + jobs) | Iceberg on S3 Tables |
|---|---|---|---|---|
| new services | none | Postgres (RDS/Aurora) for a shared catalog; none with catalog files in S3 | Glue Data Catalog (cheap); a compaction/expiry job (Glue/EMR Spark or a pyiceberg ≥0.10 task) | S3 Tables (managed compaction, expiry, orphan removal) |
| per-customer setup | key, 3 roles, task definition, EventBridge rule | + 2 catalogs (files) or 2 DB schemas + DB roles | + Glue databases and resource policies | + table bucket or namespace, table policies, key per table bucket |
| maintenance jobs | none beyond the batch (month-close compaction runs inside it) | `expire` + `cleanup` per batch, compaction monthly, daily for query cost | expiry, compaction, orphan removal: separate jobs | automatic (billed per object and GB processed) |
| fits the per-customer Fargate model | yes | yes (DuckDB in the task) | yes, with the custom FileIO | yes, through the Iceberg REST endpoint with SigV4 |
| cost at this volume | S3 only | S3 + RDS (an always-on instance; small at this size, but a second PHI datastore to patch, back up and audit) | S3 + Glue (likely within free tier for tens of customers) + job compute | S3 Tables storage premium over S3 Standard, plus monitoring and compaction charges (verify current pricing) |
| other engines (Athena, Spark, Snowflake) can read gold | as plain Parquet with the manifest (no table semantics) | DuckDB only, in practice | yes | yes, the strongest point |

## 6. Recommendation

**Keep the current design.** Reasons, in order:

1. **Security model.** Every byte the lake writes, metadata included, sits under the customer's prefix and key,
   and AWS enforces it with the IAM, bucket-policy and KMS gates already built and tested.
   - DuckLake's catalog holds PHI (measured). Moving it into Postgres breaks "role + key, or nothing" and per-key
     crypto-shredding.
   - Keeping it as per-layer files in S3 works, but brings back a hand-built CAS commit and costs 36 MB of catalog
     transfer per batch and 3.7 MB per agent request.
   - S3 Tables would replace the prefix ABAC model with a second policy language.
2. **Agent cost is what users feel.** At 120 days the current lake serves the workload with 22 requests and 60 KB
   to open, then a median of 14 requests and 3.4 MB per query. The formats only match that right after a
   compaction. Mid-month, DuckLake is at 59-73 requests and 7 MB.
3. **Per-batch savings are real but not decisive.** DuckLake writes 29 MB per batch instead of 125-160 MB and
   uses 13 s of CPU instead of 30-40 s, at a few cents a month per customer in S3 terms.
4. **Correctness is already covered.** All 29 workload answers are identical across the three designs at every
   checkpoint. The current lake's commit, crash and lock paths have regression tests. Iceberg through pyiceberg
   would lose cross-table atomicity.
5. **Iceberg's strengths don't apply here.** Its multi-engine reach, managed maintenance (S3 Tables) and
   field-id schema evolution aren't needed by a single-engine, single-writer-per-customer lake of a few GB per
   customer per year.

### Worth taking from the formats without switching

- **Merge-on-read for stragglers.** DuckLake's 4x write saving comes from deletion files instead of rewrites.
  Writing a small "superseded claim keys" file per batch, and rewriting the quarter only at month close, would cut
  the current lake's straggler rewrites (`lake-optimization.md` §7). The cost is an anti-join at read time.
- **Change-feed catch-up.** The prototypes' pending rows come from "runs after gold's silver mark", which the
  current lake already has as `silver_through`. Nothing to add.
- **Longer gold retention as an option for audits.** Keeping retired manifests (KBs) and their files for N days
  gives DuckLake-style time travel at a known storage cost. Today gold older than 20 minutes is unrecoverable
  except by a rebuild from silver.

### What would change the answer

- **Other engines must read gold** (Athena for analysts, Snowflake or Spark for a data team). Then Iceberg on S3
  Tables, one table bucket per customer with the customer key, becomes worth its second policy model.
- **Many concurrent writers per customer, streaming ingestion, or frequent schema changes.**
- **A shared catalog becomes acceptable to compliance** (Postgres with per-customer schemas and RDS IAM auth). Then
  DuckLake's atomic multi-table commits, its smaller codebase and its write savings make it the better engineering
  choice, with daily compaction to keep reads flat.

### What switching would take

- **DuckLake:**
  - two catalogs per customer (silver parser-only, gold reader-readable), as S3 files or Postgres schemas, with
    inlining set to 0 and ZSTD and row-group options set;
  - replace `lake.py` and the gold half of `pipeline.py` with the ~300 lines of `gold_flow.py` and
    `ducklake_lake.py`;
  - in `query.py`, attach the gold catalog and enable version pinning;
  - a daily compaction and expiry step, and a migration that loads existing silver with `ducklake_add_data_files`;
  - re-run `tests/test_lake.py`-equivalent tests for crash paths.
- **Iceberg:**
  - pyiceberg ≥ 0.12 or a JVM writer;
  - a KMS-capable FileIO;
  - a catalog per customer (Glue databases, or S3 Tables buckets);
  - a separate compaction and expiry job;
  - accept per-table commits, or put a gold "snapshot set" manifest on top, which is the current design again.

### Risks that remain either way

- **Current:**
  - it is hand-built code that must stay tested (the independent review found real bugs);
  - straggler rewrites grow with the open quarter (per batch writes reached 160 MB at 120 days);
  - month-close batches cost about 3,000 requests and 0.7 GB read at 120 days;
  - single-writer lease;
  - no gold time travel.
- **DuckLake:** a PHI-bearing catalog, compaction cadence driving query cost, a young format, DuckDB-only access.
- **Iceberg:** per-table commits, metadata growth (`metadata.json` and manifest lists grow with every commit
  unless expired and merged), pyiceberg gaps, and maintenance jobs to own or buy.
- **Not measured here** (same caveats as `lake-optimization.md`):
  - real S3 latency;
  - multi-year histories;
  - Postgres-catalog DuckLake, which can't be simulated here. It would remove the 36 MB per batch and the
    3.7 MB per query of catalog-file traffic, and add one DB round trip per catalog lookup;
  - S3 Tables, which is assessed on paper only; verify pricing, quotas, BAA eligibility and per-table KMS
    before relying on the table above.
