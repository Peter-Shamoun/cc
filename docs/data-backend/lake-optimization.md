# Lake optimization: Parquet + DuckDB medallion at 5 MB/day per customer

Scope: the claims lake in `docs/data-backend/design-doc.md` and `s3-folder-layout.txt`, at the target volume of
5 MB of raw EDI per business day per customer (~4,900 claims/day, ~1.2 GB raw/year). Medallion structure, schemas
(`01_lossless_edi_schema.sql`, `02_semantic_layer_views.sql`) and the security model are unchanged. What changed:
how silver and gold are laid out, how gold is maintained, and how a batch commits.

Everything here was measured with `bench/lake_bench.py` (the real pipeline, day by day, one customer, 5 MB/day,
starting 2026-01-05) and `bench/layout_bench.py` (gold layout variants). The local S3 endpoint is one Python
process (SigV4 + AES-GCM + GIL), so **requests and bytes are the portable numbers**; wall-clock is shown but
inflated. The S3 estimate is `requests × 25 ms / parallelism + bytes / 100 MB/s` (parallelism 1 for the parser's
boto3 calls, 8 for a DuckDB query).

## 1. Baseline: full gold rebuild every batch

| business days of history | 10 | 20 | 40 | 60 | 80 |
|---|---|---|---|---|---|
| requests per batch | 435 | 721 | 1,296 | 1,866 | 2,441 |
| MB read per batch | 47 | 114 | 279 | 448 | 627 |
| MB written per batch | 27 | 66 | 167 | 272 | 385 |
| gold build, s (sim) | 6.6 | 11.4 | 29.8 | 54.5 | 119 |
| parser task peak RSS | 1.6 GB | 2.8 GB | 5.4 GB | 8.6 GB | 10.7 GB |

The run was OOM-killed on the 15 GB machine at ~85 business days. Each batch loads **all** of silver (including
the raw-text spine `edi.segment`) into memory, recomputes every view and rewrites all of gold. Cost per batch is
linear in history, so total cost is quadratic:

| extrapolated (linear fit) | per batch at 1 year (250 days) | per batch at 3 years (750 days) |
|---|---|---|
| requests | ~7,300 | ~21,700 |
| read / written | ~2.0 GB / ~1.2 GB | ~6.0 GB / ~3.6 GB |
| task memory (in-memory rebuild) | ~33 GB | ~100 GB |

Not viable past a few months on any Fargate size. Two more findings from the baseline:

- **Storage blow-up.** The simulator keeps deleted objects as noncurrent versions (as versioned S3 does until
  lifecycle runs): after ~85 days the bucket held 16 GB for 0.5 GB of live data, because every batch writes a
  whole new gold version. With incremental gold most files survive between versions.
- **Reader file-size trap (DuckDB 2.0 httpfs).** Binding a view reads the first file of its list, and a remote
  file under the 2 MB cache block is fetched **whole**; the cache then serves it with one HEAD per query. Many
  small gold files therefore mean megabytes per session open. Fixed in the winner by a zero-row schema file first.

Baseline agent workload (29 `gold_sql` queries, fresh session each): 60 days median 32 requests, 1.9 MB.

## 2. Alternatives, one at a time

| step | change | effect (measured) |
|---|---|---|
| v1 | incremental gold: recompute only touched claims, rewrite only their cohort-month partitions; gold versions as manifests; ZSTD; silver parts written directly, monthly silver compaction | at 60 days: 992 req / 167 MB read / 60 MB written per batch (baseline 1,866 / 448 / 272); RSS 2.6 GB (baseline 8.6). Still growing: closure read every column of touched months |
| v2 | + read only the columns the views use for older rows (identifiers in the view SQL); fold settled months (≥3 old) into **year** files | year file rewritten nearly every batch by stragglers (16-100 claims/batch at age 3 months), so writes grew with the year: 85 MB/batch at 80 days |
| v3 | settled months fold into **quarter** files | late events rewrite at most one quarter; writes plateau (below) |
| v4 | zero-row schema file listed first in every view | median MB per agent request at 60 days 6.89 → 1.99 (baseline 1.93) |
| layout | codec, sort order, row groups, file split | see section 4 |

Losing variants were deleted from the code (year tier, cohort-month-only files).

## 3. Winner: per-batch cost stays flat

Median per batch (winner run, one customer, 5 MB/day, 4.7 MB raw per batch on average):

| business days | requests | MB read | MB written | S3 model s | sim wall s (parse / gold) | peak RSS |
|---|---|---|---|---|---|---|
| 1-20 | 372 | 19 | 8 | 10 | 10.3 (6.3 / 3.8) | 1.8 GB |
| 21-60 | 655 | 98 | 42 | 18 | 16.1 (7.4 / 7.8) | 4.3 GB max |
| 61-120 | 928 | 220 | 100 | 27 | 23.9 (8.2 / 14.4) | 5.0 GB max |
| baseline at 80 | 2,441 | 627 | 385 | 71 | 137 | 10.7 GB |

Peak RSS is the whole benchmark process: parser task, the simulated S3 endpoint and its up-to-1 GB decrypted-object
cache, and the generator. The task alone fits a 4-8 GB Fargate size.

Per batch work is bounded by the claims' activity window, not by history: what is read is the touched claims'
older rows (the last ~2-3 months of 837/835 files, view columns only) and the gold partitions holding them; what
is written is those partitions. It levels off once the open window (3 cohort months + one straggler quarter) is
full; from 60 to 120 days requests rose 924 → ~1,085 and reads 152 → 264 MB because the straggler quarter was
filling up (it peaks at 3 months, then a new quarter starts). The month-close batch costs more (retier: 2,933
requests, 426 MB read, 344 MB written, once a month).

Extrapolated to 1 and 3 years: requests per batch grow only through the claim-key lookup over monthly silver
files (+2 tables × ~3 requests per month of history: ~+70 at 1 year, ~+220 at 3 years); bytes stay at ~250 MB read
/ ~130 MB written per batch. Cumulative over 120 batches: 95,617 requests, 18.0 GB read, 8.6 GB written
(the baseline wrote 385 MB in its 80th batch alone).

Silver at 120 days: 496 MB (edi.segment 256 MB of it), 500 files. Gold sem.* is ~50 MB per cohort month
(~600 MB/year/customer), lossless.* about the same.

Agent workload (29 `gold_sql` queries; each is one production request: STS, CURRENT, manifest, views, query):

| history | layout | median requests | median MB fetched | median open+query (sim) | max open+query (sim) | S3 model median / max |
|---|---|---|---|---|---|---|
| 20 days | baseline | 23 | 2.55 | 131 ms | 236 ms | 0.10 / 0.20 s |
| 20 days | v1-v3 (small files fetched whole at bind) | 23 | 7.89 | 222 ms | 1,313 ms | 0.15 / 0.38 s |
| 60 days | baseline | 32 | 1.93 | 301 ms | 1,387 ms | 0.10 / 0.20 s |
| 60 days | v3 (before the schema-file fix) | 31 | 6.89 | 177 ms | 1,334 ms | 0.17 / 0.43 s |
| 60 days | **winner** | 33 | 1.99 | 145 ms | 239 ms | 0.15 / 0.31 s |
| 120 days | v3 (before the schema-file fix) | 50 | 3.53 | 1,241 ms | 1,657 ms | 0.18 / 0.50 s |
| 120 days | **winner** | 50 | 3.34 | 208 ms | 679 ms | 0.17 / 0.51 s |
| 120 days | baseline | (not reached: OOM at ~85 days) | | | | |

Session open in the winner: ~110-130 ms in the sim. The ~1.2 s outliers in the baseline and v3 columns were the
simulator, not the layout: its listen backlog of 5 dropped SYNs when DuckDB opened many connections at once, and
each drop cost a 1 s TCP retransmit. The endpoint now uses a backlog of 256 (`ccsim/aws/http.py`); the winner rows
were measured with it. Requests per query grow with the number of files a relation has (one HEAD + footer +
column ranges per file): at 3 years a claim relation has 3 open months + ~11 quarters ≈ 15 files, against 36 with
monthly cohorts.

## 4. Gold file layout variants

`bench/layout_bench.py` on the winner's gold after 120 business days (126 MB sem.*, 224 MB lossless.*; the same
rows rewritten each way, served by a plain ranged-HTTP server, so only the Parquet layout differs):

| variant (one knob at a time) | median / max requests per query | MB fetched, whole workload | S3 model s, whole workload | write time vs published |
|---|---|---|---|---|
| published (cohort files, ZSTD, sorted claim_key, 122,880-row groups) | 48 / 122 | 98.6 | 6.05 | 1.0x |
| one data file per relation | 39 / 84 | 72.1 | 4.80 | 1.0x |
| Snappy | 45 / 133 | 142.7 | 6.67 | 0.9x |
| ZSTD level 9 | 48 / 125 | 84.2 | 5.90 | 1.4x |
| sorted payer_key, claim_key | 48 / 125 | 85.1 | 5.88 | 1.1x |
| insertion order (unsorted) | 48 / 119 | 99.7 | 6.03 | 0.75x |
| 32k-row groups | 87 / 198 | 118.5 | 9.94 | 0.9x |
| 1M-row groups | 35 / 87 | 93.5 | 4.65 | 1.1x |
| **combined: payer_key, claim_key + 1M-row groups (adopted)** | **35 / 87** | **83.1** | **4.59** | 1.1x |

- Row-group size is the strongest knob: DuckDB issues range requests per row group and column, so fewer, larger
  groups cut requests by a quarter. Small groups are the worst option on S3.
- Sorting low-cardinality columns first (payer_key) shrinks the columns agents filter and group on.
- ZSTD over Snappy: 40% smaller files and fewer bytes fetched; level 9 saves another 15% of bytes but costs 40%
  more write time on every copy-on-write rewrite, so the default level stays.
- Fewer files help (39 vs 48 requests for one file per relation); quarter tiering gets most of that at 3 years
  while keeping rewrites small.
- Local-disk compute (DuckDB, no HTTP) was too noisy on the shared machine to rank variants (1.2-2.1 s for the
  whole workload, varying between runs). One risk to watch: with 1M-row groups a partition file under 1M rows is a
  single row group, so DuckDB scans it with one thread.

Adopted in `pipeline.py`: `GOLD_PARQUET` (ZSTD, 1,048,576-row groups) and `GOLD_SORT` (payer_key, claim_key).
Silver keeps DuckDB's default row groups, so lookups by `file_id` stay selective. The 168-day runs above were
measured before this last change; it changes gold file contents, not which files a batch rewrites.

## 5. Recommended layout

```
s3://claimclarity-prod/<customer>/
├── raw/received_date=YYYY-MM-DD/{837,835}/*.x12 + _SUCCESS     # bronze: write-once, Object Lock, unchanged
├── staging/LOCK                                                # per-customer batch lease (If-None-Match / If-Match)
├── quarantine/<run>/...
├── parquet/                                                    # silver: lossless schema, ZSTD
│   └── <table>/received_month=YYYY-MM/
│       ├── part-<run>.parquet          # one per table per batch in the open month (edi.source_file last = commit)
│       └── compacted-<run>.parquet     # closed months: one file per table, sorted by file_id, seg_ord
└── semantic_layer/                                             # gold: sem.* + PHI-stripped lossless.*
    ├── CURRENT                         # "v=<run>", compare-and-set, forward only
    ├── v=<run>/manifest.json           # the version: exact file list per relation + build state
    ├── sem.<view>/schema/part-<run>.parquet          # zero rows, listed first (cheap view binding)
    ├── sem.<view>/cohort=YYYY-MM/part-<run>.parquet  # claim relations: 3 open cohort months, one file each
    ├── sem.<view>/cohort=YYYY-Qn/part-<run>.parquet  # settled cohorts: one file per quarter
    ├── sem.{payments,provider_adjustments,payers}/all/part-<run>.parquet
    └── lossless.<table>/received=YYYY-MM|YYYY-Qn/part-<run>.parquet   # daily parts in the open month
```

Rules:

- **Claim cohort** = the month a claim key first arrived (837 or 835). Every row of a claim in the seven
  claim-keyed sem relations (remit_claims, remit_lines, claims, claim_lines, adjustments, remarks, diagnoses)
  lives in its cohort's partition, so recomputing a claim rewrites one partition per relation.
- **Incremental gold** (`GOLD_MODE=incremental`, default). Pending runs = committed silver runs newer than the
  manifest's `silver_through`. Touched claims = claim keys in their files. The build loads the pending files whole
  plus, from older silver, every row of the touched claims' subtree (`CLOSURE` in `pipeline.py`, view columns only),
  runs the unchanged view SQL, and replaces the touched claims' rows in their partitions (copy-on-write).
  Payments, provider adjustments and lossless rows are per file, so they are appended; `sem.payers` is merged.
- **Full rebuild** on `GOLD_MODE=full`, on the first build, when the rules hash changes (view SQL, ref seeds,
  ESCALATION, layout constants), or for a backfilled older month. Same layout, so either path is a valid base.
- **Commit and recovery.** A batch writes its silver parts directly (no staging copy) and `edi.source_file` last.
  Parts without that commit record are garbage; the next batch deletes them under the lock. A batch that crashed
  after its silver commit leaves `silver_through` behind; the next batch (or a redelivered event) includes it.
- **Concurrency.** One batch per customer at a time: `staging/LOCK` lease created with `If-None-Match`, taken
  over with `If-Match` after 2 h. CURRENT is still a compare-and-set, against the version the build started from.
- **Retention.** A data file is deleted once two newer versions no longer list it; lifecycle expires the
  noncurrent bytes after a day. Orphans from crashed builds are swept at month close.
- **Reader.** CURRENT → manifest (one extra GET) → views over exact file lists, schema file first. The DuckDB
  secret and `allowed_directories` are scoped to `<customer>/semantic_layer/` (was: the version prefix), because
  versions share files. Tenant isolation is unchanged (the tests check each layer on its own); a reader could now
  open files of an older version of its own customer's gold, which holds no PHI.
- **Parquet.** ZSTD (default level). Gold: 1,048,576-row groups, claim relations sorted by payer_key, claim_key.
  Silver: default row groups (122,880), closed months sorted by file_id, seg_ord. `edi.segment` is stored as is (ZSTD halves it against Snappy) and never read by gold.

## 6. Correctness

- `tests/test_lake.py::test_incremental_gold_equals_full_rebuild`: a 36-day stress-ish run (duplicate and corrupt
  deliveries, secondaries, reversals, voids) across a month close, with `OPEN_MONTHS=1` so a month folds into its
  quarter, and with one batch killed after its silver commit; every one of the 25 reader relations equals a full
  rebuild, row for row (`EXCEPT ALL` both ways).
- Same module: crashed batch caught up, closed months compacted, uncommitted parts treated as garbage, the lock.
- `tests/test_backend_e2e.py` (ledger checks and every security layer) passes unchanged.

## 7. Risks

- **Straggler cost.** A late event for a settled claim rewrites its quarter (up to ~150 MB per relation set at
  this volume). Real long tails (appeals, takebacks) make this a near-daily cost; still bounded.
- **View-column projection** reads only columns whose names appear in the view SQL text. It is a superset by
  construction, but a view that reached a column through `*` on a non-root table would silently get NULLs:
  the equality test is the guard; keep it in CI.
- **Manifest size** grows with partitions (~25 relations × (3 + quarters) files): ~100 KB at 3 years. Fine, but
  every session open fetches it.
- **Reader scope widened** from a version prefix to the customer's gold prefix (see above).
- **Not exercised here:** multipart uploads of large gold files (the local endpoint has none; real S3 needs it
  above 5 GB, boto3 switches at 8 MB), real S3 latency, and the 2×1-year / 1×3-year runs (left for the full-scale
  datasets).

## 8. Remaining ideas

- Compact the open month's silver parts weekly: most per-batch requests are the ~20 daily parts × 16 tables of
  the open month read by the closure load.
- Keep a small claim-key → file index instead of scanning `clm01`/`clp01` across all silver months each batch.
- Cache manifests per version in the app server (immutable), and set `validate_external_file_cache=NO_VALIDATION`
  for gold (files are immutable): saves a GET per session and a HEAD per file.
- Parser CPU (~1.7 s per raw MB in Python) is now the largest single batch cost.
