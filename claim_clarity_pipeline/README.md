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

## Modules

| Module | What it is |
|---|---|
| `customer_settings.py` | One customer's resource names and tunables. The pipeline is cloned per customer from this. |
| `provision_customer.py` | Onboarding: bucket, S3 event -> SQS queue + DLQ (redrive after 3 receives), the two CloudWatch alarms, App Auto Scaling policies, ECS cluster / service / task definitions, the candidate schedule, the Iceberg tables. |
| `ingestion_task.py` | The ECS parser task: SQS manager thread, downloader threads, parser processes (vCPUs - 1), one DuckDB writer, two slots, exporter. |
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

## What the runs showed (2026-11-12, AWS Emulator 1.0.322)

A run with 20 days of mock data: 189 files delivered plus 18 duplicate deliveries. The alarm started the task 14 s
after the files arrived, 187 files were committed in three slot exports (98, 101 and 6 files seen), the 18
duplicates were skipped, two unreadable files reached the dead-letter queue after three receives each, the idle
alarm stopped the task 30 s after the queue emptied, and its ephemeral storage was removed. Candidate selection
picked 13 claims, then 0 on the second run.

Design points the runs raised:

1. **A slot export is not atomic.** An Iceberg commit covers one table and silver is 25 tables. The export
   journal in `silver_lake.py` rolls an interrupted export back; without it a retry duplicates rows. It was
   exercised when exports failed mid-way (below).
2. **A failed export sends good files to the dead-letter queue.** When an export fails for a reason that is not
   the file's fault (here, S3 refusing a write), every file of the slot is released to SQS and retried; after
   three such failures all of them are dead-lettered. One run dead-lettered 50 good files this way.
3. **Each slot export writes about 100 S3 objects** (per table: a data file, a manifest, a manifest list and a
   metadata file), whatever the slot's size. Small slots multiply objects and snapshots.
4. **Onboarding starts the parser task once.** Configuring the bucket notification makes S3 send `s3:TestEvent`
   messages; the queue alarm sees them. The task deletes them and is scaled back to zero.
5. **The idle alarm needs one-minute periods in production.** `AWS/SQS` metrics are reported once a minute; the
   10 s periods here work only because the stand-in publishes high-resolution metrics.

Limits of the emulator for this work:

- **S3 is capped in the emulator's source** (`MAX_OBJECTS_PER_BUCKET = 1000`, 64 MB per bucket, 128 MB in total).
  A 30-day run hit the object cap after six exports. The emulator cannot hold a lake at scale.
- It runs no containers here and publishes no SQS metrics, and EventBridge Scheduler does not launch ECS targets
  (the stand-ins above).
