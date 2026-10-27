# Gold publish

[Overview](00-overview.md) › [Ingestion & lake](10-ingestion.md) › Gold publish

Each batch publishes a new gold version that readers can never see half-built: new data files first, then a
manifest that lists them, then a compare-and-set on the `CURRENT` pointer. Readers follow `CURRENT` to one
manifest and read exactly its files, and retired files stay for 20 minutes so open sessions finish.

![Gold publish](../png/15-gold-publish.png)

## In this diagram

| Item | What it does | Details |
|---|---|---|
| Gold build | Step 4 of the parser task: recomputes the claims the batch touched. | [Parser task](14-parser-task.md) |
| Data files | New Parquet files; a file is never overwritten. | |
| Manifest `v=<run>` | Lists every file of every relation in this version. | |
| `CURRENT` | Names the live version; moved only by compare-and-set. | |
| Reader session | DuckDB in a run worker, as Customer B's reader role, reading exactly the listed files. | [Chat turn](32-chat-turn.md) |
| Retired files | Files a newer version no longer lists. | |
| Older than 20 min? | Retired files are deleted only after every reader that saw them has expired. | |
| Deleted | The build removes them. | |

## How it works

1. The build reads `CURRENT` and its ETag, and the manifest it names.
2. It loads the new silver runs plus the older rows of the claims they touch, and runs the semantic view SQL.
3. It writes new data files, then `v=<run_id>/manifest.json`.
4. It writes `CURRENT` with `If-Match` on the ETag from step 1. If another build won, it stops.
5. It deletes files retired more than 20 minutes ago.
6. A reader reads `CURRENT`, then that manifest, then the listed files.

## Specification

| Item | Value |
|---|---|
| Relations | 10 `sem.*` relations; 15 PHI-stripped `lossless.*` tables for audited escalation, published next to gold |
| Partitions | Claim relations by cohort month (3 newest months, older months by quarter); small relations one file each |
| Format | Parquet, ZSTD, 1,048,576-row groups; a zero-row schema file listed first per relation |
| Incremental build | Default: only the touched claims are recomputed and their partitions rewritten |
| Full build | `GOLD_MODE=full`, the first build, a change to view SQL or reference data, or a late batch for an older month |
| Month close | Partitions re-tiered (months beyond the 3 newest fold into quarters); orphan files swept |
| `CURRENT` | One object, `v=<run_id>`; first write `If-None-Match: *`, then `If-Match`; only moves forward |
| Retention | Retired files deleted after 1,200 s (900 s reader credentials + 300 s); noncurrent versions expire after 1 day |
| Permissions | Parser role: Get, Put, Delete on `semantic_layer/`. Reader role: Get and List only. |
| Encryption | Customer B's lake key |
| PHI | No direct patient identifiers: person names, ids, addresses, birth dates and bank accounts are removed |
| Crash before `CURRENT` | Readers keep the old version; the new files are swept later |
| Logging | Gold build report in the run summary (CloudWatch Logs); CloudTrail for every write to `CURRENT` |

## Related

Up: [Ingestion & lake](10-ingestion.md) · Previous: [Parser task](14-parser-task.md) · See also:
[Chat turn](32-chat-turn.md) · [SQL guard and escalation](44-sql-guard-escalation.md)
