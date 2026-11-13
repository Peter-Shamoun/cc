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
| 30 days (95 MB, 738 deliveries) | 119 s, 0.79 MB/s | 39.7 s, 2.39 MB/s (before the per-file parser changes) |
| 90 days (292 MB, 1,986 deliveries) | not measured | 3.1-3.2 MB/s, first commit after 75 s, 2.1 GB peak heap |
| 90 days as 2 large files (278 MB) | not measured | 4.2 MB/s, 1.9 GB peak heap (before the per-file parser changes) |
| 3 customers at once, 30 days each | 147-149 s each | 0.87 MB/s each, 2.6 MB/s together (before the per-file parser changes) |
| 1 year (1.2 GB, 8,849 deliveries) | about 22 min, 802 Parquet files | 8.4 min, 3.1 MB/s, 9 slot exports of 1 GiB |

Memory over the year: the parser task peaks at 2.3 GB (1.3 GB in its main process, during a slot export; about
0.5 GB between exports) of its 16 GB; candidate selection at 4.8 GB of its 8 GB, with DuckDB held to half the
task's memory: DuckDB fills what it is allowed (4 GiB) and the Parquet readers add 1 to 2 GB whatever the limit
(the same query with a 1 GiB limit peaks at 3.0 GB and takes 34 s instead of 24 s). A gate checks that each task
stays within its task memory.

Settings swept on the 90-day set (parsers 1 / 3 / 7, export threads 2 / 8, slot 256 MB / 1 GiB, downloaders 2 / 4):
only the parser count moves throughput (1.2 / 2.5 / 2.7 MB/s on 4 cores), and a smaller slot brings the first
commit forward (31 s at 256 MB) for three times the Parquet files.

Changes made to the design, each verified by the suite:

1. **Slots are directories of parsed Arrow files, not DuckDB databases.** Inserting rows into a slot database and
   reading them back was 95 of the 119 s. The parser processes now write rows in the lake's column types and check
   NOT NULL columns; the exporter concatenates a table's files into its append. The DuckDB writer is gone.
2. **A slot export appends the silver tables side by side**, the commit record (`edi_source_file`) last and alone.
   25 appends one after another took about 23 s per slot.
3. **A failed export is retried inside the task** while its files' messages stay hidden. Releasing them counted a
   receive against every file of the slot and dead-lettered good files after three failures.
4. **Silver is the parsed fields, and the source of truth.** The segment text is not stored: every element value
   of every segment is in a typed row made or filled from that segment, or in `edi_misc_segment`
   (`tests/test_segment_lossless.py`). Not kept: the exact formatting of a number where the schema has no raw copy
   of it, and bytes that are not EDI data (line wrapping, padding, a byte-order mark).
5. **An export is journaled**, because an Iceberg commit covers one table and silver is 25: an interrupted export
   is rolled back before the next one. Exercised by the export_failure and task_crash scenarios.
6. **Tasks get a VPC with a route out.** ECS stops a Fargate task whose subnet cannot reach its image registry.
   The design does not cover networking; with PHI, private subnets with VPC endpoints are the likelier choice.

Open design points:

- **Small files**: 25 tables share a slot, so Parquet files average 1.2 MB at a 0.5 GiB slot and 3.5 MB at 2 GiB.
  Compaction is not built.
- **The parsers are the limit**: about 1.9 MB/s for a parser process with a core to itself, 3 MB per second of
  parser time on large files. A small file costs about 25 ms beyond its bytes: its rows become up to 16 small
  tables, each written to its own staging file.
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
