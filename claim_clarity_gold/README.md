# Gold

A materialized subset of silver for the queries people actually ask. Developed apart from the ingestion pipeline
(`claim_clarity_pipeline/`): it is built from a silver data set and benchmarked on its own.

```bash
python -m claim_clarity_gold.benchmark_gold --working-directory /tmp/gold-1 --days 30
```

## Design from first principles

The schema is derived from what people ask of 837, 835 and 277CA data, not from which columns the benchmark's
queries read. The benchmark stays as a check (can gold answer it, how fast), not as the source of the schema.

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
| `superseded_remit_claims` | event | a claim payment that is not its claim and payer's current adjudication | 835 |

Most questions are answered by a state table alone. History and detail join an event table to it by `claim_key`.
"The current adjudication's lines / reasons" is `remit_lines` or `adjustments` ANTI JOIN `superseded_remit_claims`:
the payments that are not current are about 5% of them, and one that stops being current never becomes current
again, so that relation only grows, and skipping a small set is cheaper than joining to `claim_payers` (on a year:
95 ms instead of 265 ms).

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

## Code

| File | What it is |
|---|---|
| `gold_relations.py` | the relations: event relations as column lists of the semantic views, state relations as SQL |
| `full_build.py` | gold from scratch, one Parquet file per relation: the definition the incremental build must equal |
| `incremental_gold.py` | gold kept current a silver batch at a time, as Iceberg tables; `compact()` |
| `canonical_questions.py` | questions of each group, written against gold |
| `benchmark_compatibility.py` | views that put the flags the benchmark's queries read back onto the event relations |
| `benchmark_gold.py` | the scorecard for the four criteria |
| `query_workload.py`, `silver_dataset.py` | the benchmark's queries; silver data sets and their daily batches |

```bash
python -m claim_clarity_gold.benchmark_gold --working-directory /tmp/gold-1 --days 30 [--compare-with-silver]
```

A batch: append the batch's event rows (the seven tables commit side by side); compute the batch's part of the
state; read the previous state of the claims it names from the month partitions they are in (a key index says
which month, and the rank of each key's top event); fold; replace those month partitions. Its cost follows the batch
and the size of a month. `compact()` merges each month partition's files into one sorted file, expires old
snapshots and deletes unused data files.

## Scorecard (30 days of silver, 95 MB of raw EDI, before 277CA)

| Criterion | Result |
|---|---|
| 1. Subset | every relation is defined over the semantic views over silver; the incremental build (21 daily batches) equals the full build in all nine relations |
| 2. Easy | 11 relations; the 17 canonical questions are 3 to 15 lines each |
| 3. Fast | canonical questions: median 6 ms, at most 39 ms. The benchmark's 156 queries: gold covers 149 (median 55 ms through the compatibility views, against 343 ms on the views over silver); all 149 equal the full build, and 148 equal silver (`a_838170f50b` sums floating-point values, and the order of summation shows in the last digit) |
| 4. Build cost | 2.3 s per batch in the first third, 3.4 s in the last; gold is 21.4 MB in 18 files after compaction (silver: 58 MB); key index 17 MB |

With 277CA (30 days generated by `python -m ccsim.edi_mock <out> --days 30 --mb-per-day 5 --tenants acme_labs
--seed 7`; 751 277CA files): the incremental build equals the full build in all ten relations, and all 149 covered
benchmark queries equal silver. Canonical questions: median 5 ms, at most 41 ms. 3.0 s per batch in the first
third, 4.1 s in the last; 23.7 MB in 10 files after compaction.

Over a year (255 daily batches, local catalog, before 277CA) a batch's cost does not grow with silver:

| Quarter of the year | Seconds per batch | State month partitions replaced | State rows replaced | Event rows appended |
|---|---|---|---|---|
| 1 | 5.2 | 4.0 | 13,900 | 65,800 |
| 2 | 5.4 | 4.0 | 12,900 | 67,000 |
| 3 | 4.8 | 4.0 | 12,100 | 61,700 |
| 4 | 4.8 | 4.0 | 12,900 | 63,100 |

What a batch costs depends on how many state month partitions it touches (a claim's state lives in the month of its
first activity, and a batch touches the months of the claims it names): 3.7 s at 2, 5.0 s at 4, 5.9 to 7.2 s at
6, the same in the first half of the year as in the second. An earlier figure (4.4 s in the first third, 5.7 s in
the last) came from a sample of batches and is withdrawn. Compaction: 8 to 14 s per run.
Questions on a year of gold (the 14 that do not need 277CA data, which the year set lacks): median 19 ms; the
slowest are top denial reasons (137 ms) and allowed and paid by procedure (89 ms), the two that read the current
adjudication's adjustments and lines; joining them to `claim_payers` instead took 233 ms and 277 ms.
The result equals the full build in all nine relations (17.4 million rows); 272 MB in 115 files (silver: 748 MB);
key index 194 MB.

On the emulator's S3 and Glue, as silver is stored (`--on-emulator-s3`; 30 days with 277CA, 21 batches): the
incremental build equals the full build in all eleven relations, and the 149 covered benchmark queries return the
full build's rows. 7.0 s per batch in the first third, 9.1 s in the last (the emulator's S3 and Glue calls are most
of the difference from local disk); compaction 23 s; questions read through S3: median 22 ms, at most 141 ms.

Open:

- The full build over a year needs one thread and spilling to disk to fit in 5 GB; it is a check, not part of
  running gold.
- The key index is 194 MB for a year, most of gold's size again: a row per claim and per claim and payer.
- The storage format of the key index: build state beside the catalog, read by key.
