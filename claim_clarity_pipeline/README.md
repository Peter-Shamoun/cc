# Claim Clarity pipeline on the AWS Emulator

The ingestion pipeline and candidate selection from the design, built with plain boto3 / pyiceberg / DuckDB and
run against the [AWS Emulator](https://github.com/aws/aws-emulator) to see whether the design holds.

```bash
aws-emulator --telemetry off &                       # serves https://127.0.0.1:9297, state in memory
pip install -e ".[dev]"
python -m claim_clarity_pipeline.run_emulation --working-directory /tmp/claim-clarity-emulation
pytest tests/test_claim_clarity_pipeline.py          # the same run, as a test (skipped without an emulator)
```

`run_emulation` onboards a customer, generates mock 837P/835 files (`ccsim.edi_mock`) into an SFTP incoming
directory, moves them to S3 with duplicate deliveries and one corrupt file, and then only watches: the alarms start
the parser task, the queue drains into the lake, the corrupt file reaches the dead-letter queue, the idle alarm
stops the task. It then fires candidate selection twice and moves the CSV to SFTP outgoing. It writes
`emulation_report.json` (timeline of queue depth, alarm states, desired count; lake contents; candidate runs) and
fails if the result is not what the design promises (`check_report`).

## Evaluation suite

```bash
python -m claim_clarity_pipeline.evaluation.run_evaluation --working-directory /tmp/evaluation-1
    [--scenarios control_plane,export_failure,task_crash,throughput] [--throughput-days 7]
    [--set slot_size_limit_bytes=268435456 --set downloader_thread_count=8 ...]
```

It writes `scorecard.json` and exits non-zero if a gate fails. Gates are the design's guarantees (each good file's
content in the lake exactly once, only unreadable files dead-lettered, no export journal left, ephemeral storage
gone with the task, a failed export retried without dead-lettering, a crashed task's work finished by its
replacement). Scores are what a design change should move: MB/s, seconds to first commit, export time, S3 objects
and Parquet files written, snapshot count, peak ephemeral disk, peak task memory, messages in flight, candidate
selection time. A design candidate is a code change or a `--set` of any `CustomerPipelineSettings` field; compare
scorecards. `evaluation/fault_injecting_ingestion_task.py` is the parser task with failing or fatal lake appends.

## Modules

| Module | What it is |
|---|---|
| `customer_settings.py` | One customer's resource names and tunables. The pipeline is cloned per customer from this. |
| `provision_customer.py` | Onboarding: bucket, S3 event -> SQS queue + DLQ (redrive after 3 receives), the two CloudWatch alarms, App Auto Scaling policies, ECS cluster / service / task definitions, the candidate schedule, the Iceberg tables. |
| `ingestion_task.py` | The ECS parser task: SQS manager thread, downloader threads, parser processes (vCPUs - 1), stager, two slots, exporter. |
| `silver_lake.py` | Silver as Iceberg tables in a Glue catalog: one per lossless table, `edi_source_file` appended last as the commit record, plus the candidate tables. Slot export with dedup and an export journal. |
| `candidate_selection_task.py` | The one-shot task: re-export unexported runs, load the preset, query silver in DuckDB, commit candidates with the run id, write the CSV, record the export. |
| `emulator_stand_ins.py` | What is outside the pipeline or missing from the emulator (below). |
| `emulator_connection.py` | Sets the standard AWS environment variables for the emulator. The pipeline code never mentions it. |
| `run_emulation.py` | The end-to-end run and its checks. |

The X12 parser, the lossless schema and the semantic views are the existing ones (`ccsim/parser.py`,
`claude/schema/`).

## What the emulator does and what stands in for it

Done by the emulator itself: S3 event notifications to SQS with the prefix filter, visibility timeouts, redrive to
the dead-letter queue, alarm evaluation (including metric math), alarm actions invoking App Auto Scaling step
policies, the ECS service's desired count starting and stopping service tasks, Glue as the Iceberg catalog, S3.

Stand-ins (`emulator_stand_ins.py`), each replacing something AWS or IT does:

| Stand-in | Replaces | Why |
|---|---|---|
| `publish_queue_depth_metrics` | SQS reporting its depth to CloudWatch | The emulator publishes no `AWS/SQS` metrics, and `PutMetricData` refuses `AWS/` namespaces, so the alarms read the same metric names from `ClaimClarity/EmulatedSQS`. |
| `LocalFargate` | Fargate running the container on ephemeral storage | The emulator records tasks but cannot pull or run an image here. Each ECS task runs as a local process with its own directory, removed when ECS stops the task. |
| `fire_schedule_now` | EventBridge Scheduler's launch | The emulator stores the schedule but never launches its ECS target. The stand-in calls RunTask with the schedule's own target. |
| `move_sftp_incoming_to_s3_input`, `move_s3_output_to_sftp_outgoing` | IT's mover jobs and the on-prem SFTP server | Outside AWS. Local directories. |

The run shortens the timings (alarm periods of 10 s, a 20 s visibility timeout, 8 MB slots);
`CustomerPipelineSettings` holds the production-shaped defaults.

## Choices the design left open

- **Iceberg catalog:** Glue. Silver is one Iceberg table per lossless table (25), unpartitioned for now.
- **Dedup key:** the SHA-256 of the file's bytes, compared with `edi_source_file` in the lake and with earlier
  files in the same slot. A duplicate delivery is skipped, counted in `edi_ingest_run`, and its SQS message deleted.
- **A slot is exported** at its size limit, or when no file is left to stage (nothing between SQS and the writer,
  and no new file for `slot_idle_export_seconds`).
- **Candidate preset:** a JSON object in S3 with a DuckDB query and its parameters. The bucket is versioned and
  each run records the preset version it used. The query sees the lossless tables and the semantic views computed
  from silver at query time (`sem.claims.is_latest_submission` is "the latest version of each claim").
- **Candidate tracking:** `candidate_selection` (one row per selected claim, with the run id and the row as it
  goes into the CSV) and `candidate_export` (one row per run whose CSV was written). A run with rows in the first
  and none in the second is re-exported at the start of the next run.

## What the evaluation showed (2026-11-12)

Benchmarks on a 4-core machine, emulator built from source with its S3 and SQS caps raised (below). All gates
pass in every row. "Baseline" is the design as drawn: a DuckDB writer inserting into DuckDB slots, 25 Iceberg
appends one after another.

| Workload | Baseline | Now |
|---|---|---|
| 30 days (95 MB, 738 deliveries) | 119 s, 0.79 MB/s | 39.7 s, 2.39 MB/s |
| 30 days as 20x larger files (93 deliveries) | not measured | 23.7 s, 4.0 MB/s (before segment text was added) |
| 3 customers at once, 30 days each | 147-149 s each | 79-81 s each (before segment text was added) |
| 90 days (292 MB), slot 0.5 / 2 / 4 GiB | not measured | 113.6 / 92.4 / 106.0 s |
| 1 year (1.2 GB, 8,849 deliveries) | about 22 min | about 11 min (slot 0.5 GiB, before segment text was added) |

Changes made to the design, each verified by the suite:

1. **Slots are directories of parsed Arrow files, not DuckDB databases.** Inserting rows into a slot database and
   reading them back was 95 of the 119 s. The parser processes now write rows in the lake's column types and check
   NOT NULL columns; the exporter concatenates a table's files into its append. The DuckDB writer is gone.
2. **A slot export appends the silver tables side by side**, the commit record (`edi_source_file`) last and alone.
   25 appends one after another took about 23 s per slot.
3. **A failed export is retried inside the task** while its files' messages stay hidden. Releasing them counted a
   receive against every file of the slot and dead-lettered good files after three failures.
4. **Silver keeps every byte of the raw file.** `edi_segment` carries each segment as received (`raw_text`) and
   the bytes up to the next segment (`separator_after`); `edi_source_file` carries the bytes before the first
   segment (`leading_bytes`). A gate rebuilds every file from the lake alone and compares its SHA-256 with the one
   recorded at ingest. Silver went from 85% to 105% of the raw size.
5. **An export is journaled**, because an Iceberg commit covers one table and silver is 25: an interrupted export
   is rolled back before the next one. Exercised by the export_failure and task_crash scenarios.
6. **Tasks get a VPC with a route out.** ECS stops a Fargate task whose subnet cannot reach its image registry.
   The design does not cover networking; with PHI, private subnets with VPC endpoints are the likelier choice.

Open design points:

- **Silver stores the content twice**: as segment text and in the typed tables. A single segment table with the
  elements as an array measured 38 MB for the 30-day set (typed tables: 80 MB) and about 9 s of CPU to produce
  (typed parsing: about 55 s). The typed tables would then be views over it, or part of gold.
- **Small files**: 25 tables share a slot, so Parquet files average 1.2 MB at a 0.5 GiB slot and 3.5 MB at 2 GiB.
  Compaction is not built.
- **The parsers are the limit** once the above is in place: about 1.6 MB/s per parser process.
- **Onboarding starts the parser task once**: configuring the bucket notification sends `s3:TestEvent` messages.
- **Alarm periods**: `AWS/SQS` metrics are per minute, so start-up and scale-down each take a minute or more in
  production; the runs here use 10 s periods on high-resolution metrics.

Limits of the emulator for this work:

- **The emulator's capacity limits are constants in its source**, sized for small tests: S3 1,000 objects and
  64 MB per bucket (128 MB in total), SQS 1,000 messages per queue (further S3 events are dropped silently), 32
  S3 event deliveries at once, 512 ECS tasks. The runs above use a build from source with those raised (S3 to
  10 GiB and 5,000,000 objects, SQS to 2,000,000 messages) and the router trimmed to the 29 service crates this
  pipeline reaches: 9 minutes and 8.5 GB of disk to build. S3 objects are held in memory.
- It runs no containers here, does not replace a service task whose container exited, publishes no SQS
  metrics, and EventBridge Scheduler does not launch ECS targets
  (the stand-ins above).
