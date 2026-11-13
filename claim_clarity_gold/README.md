# Gold

A materialized subset of silver for the queries people actually ask. Developed apart from the ingestion pipeline
(`claim_clarity_pipeline/`): it is built from a silver data set and benchmarked on its own.

```bash
python -m claim_clarity_gold.benchmark_gold --working-directory /tmp/gold-1 --days 30
```

## Design from first principles (v2, being built)

The schema below is derived from what people ask of 837, 835 and 277CA data, not from which columns the
benchmark's queries read. The benchmark stays as a check (can gold answer it, how fast), not as the source of the
schema. The sections after this one describe v1, which was derived from the benchmark and is what the code builds
today.

**What people ask.** A claim goes through a life: submitted (837), acknowledged or rejected at the front end
(277CA), adjudicated by each payer in turn (835: paid, denied, adjusted), sometimes reversed or reworked, and the
money arrives in payments that have to reconcile. The questions fall into five groups:

| Question | Examples | Grain it needs |
|---|---|---|
| Where is the claim now? | open claims by age, days to pay, awaiting acknowledgment, timely filing | one row per claim, per claim and payer |
| How much did we get? | billed / allowed / paid / patient share by payer, test, provider, month | claim and payer; service line |
| Why not? | denial rate and reasons by payer, test, diagnosis; front-end rejections | claim and payer; adjustment; acknowledgment |
| What happened along the way? | resubmissions, reversals and takebacks, secondary payers | each submission, each remittance |
| Does the cash reconcile? | payments, provider-level adjustments | payment |

**Two kinds of table.** The first three groups ask for the claim's current state; the last two ask for history.
So gold has state tables, one row per thing holding the current truth, and event tables, one row per thing that
happened, never changed after it is written.

| Relation | Kind | One row is | From |
|---|---|---|---|
| `claims` | state | a claim: its latest submission, its primary payer's answer, totals over all payers | 837, 835, 277CA |
| `claim_payers` | state | a claim and a payer: what was sent, whether it was acknowledged, the current adjudication | 837, 835, 277CA |
| `claim_submissions` | event | one 837 claim submission (CLM) | 837 |
| `claim_lines` | event | one 837 service line (SV1) of a submission | 837 |
| `claim_acknowledgments` | event | one 277CA claim status (STC) | 277CA |
| `remit_claims` | event | one 835 claim payment (CLP) | 835 |
| `remit_lines` | event | one 835 service line (SVC) | 835 |
| `adjustments` | event | one 835 adjustment (CAS group, reason, amount) | 835 |
| `payments` | event | one 835 payment (BPR / TRN) | 835 |
| `provider_adjustments` | event | one 835 provider-level adjustment (PLB) | 835 |

Most questions are answered by a state table alone. History and detail join an event table to it by `claim_key`,
or by `remit_claim_id = claim_payers.current_remit_claim_id` for "the current adjudication's lines / reasons".

**What changes from v1.**

- Event rows carry no flag that a later file can flip (`is_current_adjudication`, `is_latest_submission`,
  `adjudication_state`). "Current" lives in the state tables. Event tables are then append-only, which is the
  cheapest thing an Iceberg table can be, and v1's largest build cost (demoting older rows) disappears.
- State tables are small (a row per claim, per claim and payer) and partitioned by the month of the claim's first
  activity. A batch replaces the partitions of the claims it names: its cost follows the batch and the size of a
  month, not the size of silver.
- Columns are chosen by grain: identifiers, parties (payer, providers), dates on each basis (service, submission,
  acknowledgment, payment), codes (procedure, modifiers, diagnosis, status, reason) and amounts. Envelopes,
  control numbers, addresses and contacts stay in silver.
- Storage is Iceberg tables in the customer's S3 bucket, like silver.
- `claims` in v1 was one row per submission; that relation is `claim_submissions` here, and `claims` is what the
  name says.

## What gold is

1. **A subset of silver.** Each gold relation is a projection of the semantic view of the same name
   (`claude/schema/02_semantic_layer_views.sql`), which is defined over silver. Gold holds the columns the query
   workload reads, the identifiers that join the relations, and `source_file_id`.
2. **Easy to query.** Seven relations named after what they hold; one row is one EDI thing (a CLP, an SVC, a CLM,
   an SV1, a CAS, a BPR/TRN, a PLB). An eighth, `claim_payers`, is merged from `claims` and `remit_claims`: one
   row per claim and payer with what was submitted and what the payer has answered (see below).
3. **Fast.** Sorted Parquet, no joins to silver.
4. **Cheap to keep current.** `incremental_build.py` applies one silver batch at a time: the batch's gold rows
   from the batch's silver rows, a key-index lookup for the top-ranked event each claim had before, a flag flip on
   whichever side loses, an insert. Only two flags ever change on an existing row (`is_current_adjudication` and
   `is_latest_submission`).

Gold is stored as merge-on-read tables partitioned by month: an UPDATE writes the changed rows and a delete marker
and leaves the data file alone, and `compact()` merges the small files of daily inserts into one per relation per
month. The store here is DuckLake (Parquet plus a catalog, read and written by DuckDB); it stands in for any
merge-on-read format, Iceberg included, and is not a decision about gold's production format.

## claim_payers

55 of the 156 workload queries join `claims` to `remit_claims` per claim and payer, most to ask when the claim was
first submitted, whether it has a current adjudication, and what was paid. `claim_payers` holds those answers
(`gold_schema.CLAIM_PAYERS_SQL` is its definition over the two relations): the payer's responsibility, first and last submission dates and
count, the claim's latest charge and frequency code, first remit date and count, and the current adjudication's
payment date, amounts and denial flag: 20 columns.

Its rows change as files arrive, so the incremental build keeps each row in the key index, folds a batch into the
rows of the claims and payers the batch names, and replaces those rows in gold. It is partitioned by
`first_activity_month`, so a replacement opens only the months the replaced rows are in.

Over a year of daily batches it equals its definition computed from scratch, and keeping it current takes 0.5 s
of a 2.8 s batch, the same early in the year and late. It adds 168 MB to the key index (386 MB in all, against
223 MB of gold data).

`claim_payers_rewrites.json` holds 25 of those 55 queries rewritten against it, each returning the same rows as
the original on a year of gold: 1.9 s in total instead of 4.6 s, median 63 ms instead of 160 ms, in 29% less SQL.
The benchmarks ask gold those 25 in their rewritten form. Of the other 30, 12 compare individual remits or
submissions and do not fit a row per claim and payer; the rest each need a column it does not have.

Two things to know when querying it: `claim_charge_amount` and `claim_frequency_code` are the claim's, repeated on
every payer row, so summing the charge over rows counts a multi-payer claim more than once; and
`first_remit_date` counts every remit, reversals and duplicate-claim denials included.

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

On a year of silver (1.2 GB of raw EDI, 16.4 million gold rows), applied one day at a time (255 batches,
compaction every 30):

| Measure | Result |
|---|---|
| Equals a full rebuild | yes, in all eight relations |
| A whole batch | 2.8 s on average in the first third of the year, 2.7 s in the last, `claim_payers` included |
| Computing a batch's rows | 1.15 s per batch, the same in the first month and the last |
| Key index | 0.06 s per batch |
| Inserting the batch | 0.15 s per batch |
| Demoting older rows | 0.6 to 1.9 s per batch: 4 to 6 ms per demoted row all year (a batch demotes 116 to 428) |
| Compaction | 43 s over the year in nine runs, 7 s at most |
| Queries | median 77 ms over 90 files, 15.0 s for all 155 (100 ms and 20.0 s before `claim_payers`) |
| Size | 223 MB of data and a 14 MB catalog; silver is 748 MB |

What the year runs ruled out:

- **Plain Parquet files, rewritten to flip a flag**: 233 MB written for new rows and 5.1 GB rewritten to demote
  59,669 older rows (0.4% of gold).
- **The store's default compaction**: it rewrites a file only when most of its rows are deleted, which left 2,186
  files, a median query of 178 ms and a demotion cost that crept up through the year. `compact()` rewrites any
  file with a delete marker.

Open points:

- **The key index is 386 MB**, more than gold's data: the top event per claim and payer and per claim, and a copy
  of every `claim_payers` row. It is build state, read by key.
- **Two workload queries** (`a_bafe309836`, `a_258e64fde0`) return other rows on the month-partitioned layout than
  on a single file while the relations are equal: their `row_number()` orderings have ties, so their results
  depend on row order.

## Modules

| Module | What it is |
|---|---|
| `query_workload.py` | The workload and what each query reads |
| `gold_schema.py` | The seven relations and their columns |
| `silver_dataset.py` | Silver in a DuckDB file, parsed from mock EDI |
| `build_gold.py` | The full build, and a session over a gold directory |
| `incremental_build.py` | The per-batch build, the merge-on-read store and its key index |
| `benchmark_gold.py` | The scorecard |
| `benchmark_incremental.py` | Seconds per batch against gold's size, and the check against a full rebuild |
