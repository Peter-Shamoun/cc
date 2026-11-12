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
4. **Cheap to keep current.** Not yet: the build is a full rebuild. The incremental build is the next step.

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

## Scorecard (30 days of silver, 95 MB of raw EDI)

| Criterion | Result |
|---|---|
| 1. Subset | every gold column is a semantic-view column over silver; gold answers 155 of 156 queries |
| 2. Easy | 7 relations, 111 columns (semantic layer: 10 and 236; silver: 24 tables) |
| 3. Fast | all 155 queries return the same rows as the views over silver; median 23 ms against 837 ms |
| 4. Build cost | 4.5 s, a full rebuild; gold is 15.1 MB, 26% of silver's 58.1 MB |

## Modules

| Module | What it is |
|---|---|
| `query_workload.py` | The workload and what each query reads |
| `gold_schema.py` | The seven relations and their columns |
| `silver_dataset.py` | Silver in a DuckDB file, parsed from mock EDI |
| `build_gold.py` | The full build, and a session over a gold directory |
| `benchmark_gold.py` | The scorecard |
