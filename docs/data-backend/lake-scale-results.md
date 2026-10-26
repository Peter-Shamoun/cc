# Lake scale run: 2 customers, 110 batches (paused)

Run `scale-2x1y` (`bench/lake_bench.py --days 365 --customers acme_labs,acme_diagnostics --purge-raw --resume`), commit
`ff8f20d`, generator 3.3 at about 5 MB raw per business day per customer. Paused after 110 of about 520 batches
(about 55 business days per customer) so its CPU goes to the analyzer search; it resumes from
`.ccsim/lake-scale-2x1y-365d.snapshot` with `--resume`. The 1 customer × 3 year run was not started.

## Per-batch cost grows linearly with history

Medians per window of 20 batches (one batch = one customer's business day):

| batches | batch s | gold s | S3 requests | peak RSS MB | raw MB |
|---|---|---|---|---|---|
| 1–20 | 11.7 | 7.6 | 251 | 2,317 | 4.4 |
| 21–40 | 15.7 | 10.5 | 348 | 3,261 | 5.0 |
| 41–60 | 19.1 | 13.9 | 423 | 3,789 | 5.1 |
| 61–80 | 22.4 | 16.7 | 517 | 4,242 | 5.2 |
| 81–100 | 27.6 | 20.9 | 583 | 4,397 | 5.1 |

The day's input is flat (5 MB), but gold time grows by about 3.3 s and requests by about 85 for every 10 business
days of history per customer. Peak RSS is flattening (+150 MB over the last 40 batches). Batches 101–110 ran at
26–38 s while the machine was CPU-bound by the eval runner, so they are not comparable.

Linear extrapolation (planning numbers, not measurements):

| history per customer | gold s per daily batch | batch s |
|---|---|---|
| 1 year (260 business days) | about 90 | about 100 |
| 3 years (780 business days) | about 260 | about 280 |

Minutes per customer per day is affordable for a nightly job, so this is not a blocker for the design. It does mean
incremental gold is not yet proportional to the day's change: something in it reads or rewrites state in proportion
to total history (candidates: the claim-key lookup over every live silver file, cohort partitions that a day touches
growing with history, manifest work). Profile one late batch before production; the claim-key index measured in the
lake bench (96 → 2 requests at 60 days) is the first thing to try.

## Query workload at 20 business days

29 analyst queries through a reader session over S3: open + query median 169 ms, max 320 ms; median 28 requests,
0.27 MB read. Warm local-disk median 5 ms. Gold at that point: 214 Parquet files, 58–64 MB per customer.

## Operations

- The machine restarts about hourly. Snapshots every 20 batches plus `--resume` made the run survive them; a resume
  replays the generator for the ingested days (about 10–15 minutes at 100 batches).
- Found and fixed on the way: the claim-lookup probe failed once open-month compaction folded the newest run's parts
  (ff8f20d), and `sem.claims.latest_claim_status` broke ties arbitrarily when payers adjudicated on the same day
  (634fe18).
