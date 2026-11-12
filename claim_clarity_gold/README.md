# Gold

A materialized subset of silver for the queries people actually ask. Developed apart from the ingestion pipeline
(`claim_clarity_pipeline/`): it is built from a silver data set and benchmarked on its own.

```bash
python -m claim_clarity_gold.benchmark_gold --working-directory /tmp/gold-1 --days 30
```

## What gold is

1. **A subset of silver.** Each gold relation is a projection of the semantic view of the same name
   (`claude/schema/02_semantic_layer_views.sql`), which is defined over silver. Gold holds the columns the query
   workload reads, the identifiers that join the relations, and `source_file_id`.
2. **Easy to query.** Seven relations named after what they hold; one row is one EDI thing (a CLP, an SVC, a CLM,
   an SV1, a CAS, a BPR/TRN, a PLB).
3. **Fast.** Sorted Parquet, no joins to silver.
4. **Cheap to keep current.** `incremental_build.py` applies one silver batch at a time: the batch's gold rows
   from the batch's silver rows, a key-index lookup for the top-ranked event each claim had before, a flag flip on
   whichever side loses, one new file per relation. Only two flags ever change on an existing row
   (`is_current_adjudication` and `is_latest_submission`).

Gold is not a fallback and not a de-identified copy. A query gold cannot answer goes to silver.

## The workload

`query_workload.py` reads the benchmark's 156 authored questions (`bench/authored_questions.json`), whose queries
are written against the semantic views, and finds the relations and columns each one reads.

| Semantic relation | Queries that read it | Columns read / columns it has | In gold |
|---|---|---|---|
| remit_claims | 111 | 28 / 37 | yes |
| claims | 69 | 16 / 39 | yes |
| remit_lines | 33 | 21 / 39 | yes |
| payments | 11 | 9 / 16 | yes |
| adjustments | 10 | 10 / 25 | yes |
| claim_lines | 9 | 12 / 38 | yes |
| provider_adjustments | 4 | 6 / 11 | yes |
| diagnoses | 1 | 1 / 11 | no |
| payers, remarks | 0 | 0 | no |

Underneath, those queries reach 16 of silver's 24 tables and about 80 of their 370 columns; no query reaches
`edi.segment`, the envelopes, contacts or the miscellaneous segments.

## Scorecard

On 30 days of silver (95 MB of raw EDI), full build:

| Criterion | Result |
|---|---|
| 1. Subset | every gold column is a semantic-view column over silver; gold answers 155 of 156 queries |
| 2. Easy | 7 relations, 111 columns (semantic layer: 10 and 236; silver: 24 tables) |
| 3. Fast | all 155 queries return the same rows as the views over silver; median 23 ms against 837 ms |
| 4. Build cost | gold is 15.1 MB, 26% of silver's 58.1 MB |

On a year of silver (1.2 GB of raw EDI, 16.4 million gold rows), applied one day at a time (255 batches):

| Measure | Result |
|---|---|
| Equals a full rebuild | yes, in all seven relations |
| Computing a batch's rows | 1.15 s per batch, the same in the first month and the last |
| Key index | 0.06 s per batch, flat |
| Rewriting older files | 0.8 to 3.2 s per batch: it follows the rows a batch demotes (116 to 428), not gold's size |
| A whole batch | 2.7 s on average in the first third of the year, 3.3 s in the last; a full rebuild takes 37 s at that size |
| Queries | median 98 ms over 364 files (batches older than 45 days merged per month); one file per relation: 77 ms |
| Size | gold 193 MB, 26% of silver's 748 MB |

Open points:

- **Demoting a row rewrites its file.** Over the year 233 MB was written for new rows and 5.1 GB rewritten to flip
  the flags of 59,669 older rows (0.4% of gold). The cost is bounded by a day's or a month's file, not by gold, but
  it is 22 times the new data. An Iceberg writer with merge-on-read deletes a row and inserts its new version
  without rewriting the file; this prototype rewrites plain Parquet. 87% of demoted rows are less than 45 days
  old, 97% less than 60.
- **The key index is 209 MB**, larger than gold's own data: a row per claim and payer, and per claim.
- **One workload query** (`a_bafe309836`) returns other rows on the incremental layout than on a single file while
  the relations are equal: its `row_number()` ordering has ties, so its result depends on row order.

## Modules

| Module | What it is |
|---|---|
| `query_workload.py` | The workload and what each query reads |
| `gold_schema.py` | The seven relations and their columns |
| `silver_dataset.py` | Silver in a DuckDB file, parsed from mock EDI |
| `build_gold.py` | The full build, and a session over a gold directory |
| `incremental_build.py` | The per-batch build and its key index |
| `benchmark_gold.py` | The scorecard |
| `benchmark_incremental.py` | Seconds per batch against gold's size, and the check against a full rebuild |
