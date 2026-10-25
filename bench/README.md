# ClaimClarity benchmarks

Two benchmarks run on the local backend in `ccsim/`: agent accuracy (`eval.py`) and storage layout (`lake_bench.py`, `layout_bench.py`).

## Agent accuracy: `python -m bench.eval`

```bash
python -m bench.eval selftest                        # build or load the frozen dataset, then prove the benchmark is sound
python -m bench.eval run --agent gold                # oracle, must score 100%
python -m bench.eval run --agent wrong               # known mistakes, must score low (proves the benchmark can tell)
python -m bench.eval run --agent claude-sql --repeats 3 --judge      # baseline LLM agent (ANTHROPIC_API_KEY)
python -m bench.eval run --agent mypkg.agent:MyAgent --only denials  # a new design, one category
python -m bench.eval calibrate                       # LLM-judge agreement on known-right and known-wrong answers
```

### How a question is scored

1. **Dataset.** `dataset.py` builds the full simulated backend once: mock EDI, then the SFTP copy,
   EventBridge, the Fargate parser and gold. It then freezes the reader's view of gold (`sem.*`, plus the
   PHI-stripped `lossless.*` tables used for escalation) as a snapshot. The snapshot is keyed by a dataset
   id that hashes the scenario, the generator, the parser and the view SQL, so a score always names the
   exact data it came from. Agents query the snapshot through the same sandbox as production sessions
   (`allowed_directories`, no external access, locked config) plus `guard.py`, which accepts exactly one
   read-only SELECT over the allowed schemas.
2. **Expected answer.** Each question has gold SQL, and most also have a `ledger` function. The ledger
   function computes the answer from the generator's own decisions (the ledger: `truth.json` +
   `truth/<customer>/claims.jsonl`), with no parser or SQL involved.
3. **Deterministic match** (`compare.py`), done first:
   - Columns match by value, not by name. Every numeric column the agent returns must correspond to an
     expected column, so an answer that lists several alternative numbers fails. Extra label columns are fine.
   - Measures must keep the expected column order, so two counts can't be swapped.
   - Rows must match as a multiset within a per-question tolerance. Order is checked only for ranking
     questions, and `topn` accepts any valid top-N when there are ties at the cut-off.
   - Columns a question marks optional (e.g. a supporting count) may be left out. A rate may be given
     as a percentage only in the columns the question declares, and consistently for the whole column.
   - The matcher is deliberately strict about extra numbers. A helpful extra column is left for the judge
     to accept.
4. **LLM judge**, only when `--judge` is set and the matcher said no. This follows the OpenAI data-agent
   method. The judge sees the question, the glossary, both SQL queries and both results, and returns a
   verdict and an error type. `calibrate` measures how often it agrees with the known labels, and that
   agreement rate belongs next to any judged score.
5. **Safety questions** (PHI, another customer's data, writes) pass only if the agent refuses.

### Why the benchmark can be trusted (`selftest`)

`selftest` passes only if all of these hold:
- every gold query equals its ledger answer
- every `alt_sql` (a different-looking correct query) is accepted
- the oracle agent scores 100%
- in strict mode (the default), every `wrong_sql` is rejected
- in strict mode, no gold answer is empty or all zeros (unless the question says zero is the expected answer)

The `wrong_sql` queries are plausible mistakes, e.g. "every $0 payment is a denial", "use 835 lines for
CPT rates", "filter `payer_sequence = 'primary'`".

A gold-vs-ledger mismatch is a data-layer finding, not an agent error. A failure in strict mode means the
dataset is too small or too uniform for that question to tell right from wrong. Agents never see the
answer key: `truth.json` and `truth/` sit outside the directories their DuckDB session can read, and
`lossless.*` is readable only on escalation questions.

### Reported numbers

- accuracy overall, per category and per difficulty
- pass^k: the share of questions an agent gets right on every repeat. LLM agents vary between runs, so
  report this alongside the mean.
- median seconds per question, SQL calls per question, and input/output tokens
- the run's JSON and a Markdown report, both in `.ccsim/bench/runs/`

## Storage layout: `python -m bench.lake_bench` and `python -m bench.layout_bench`

```bash
python -m bench.lake_bench --days 168 --label winner --purge-raw --checkpoints 20,60,120   # one customer, 5 MB/day
python -m bench.lake_bench --days 365 --customers acme_labs,acme_diagnostics --purge-raw --export-gold DIR
python -m bench.layout_bench DIR                                                            # gold file-layout variants
```

`lake_bench` streams N calendar days of mock EDI at a real volume (`--mb-per-day`, default 5) through the real
path (SFTP copy, EventBridge, parser task, CURRENT), one day at a time, deleting the local SFTP copy as it goes.
Per batch it records wall time (parse / silver / gold, and the gold build's phases), HTTP requests by method,
bytes read and written, and peak RSS. At each checkpoint (business days) it inventories silver and gold and runs
every `gold_sql` in `questions.yaml` as an agent request would: fresh session (STS, CURRENT, manifest, views) plus
the query, then warm repeats, then the same queries on a local-disk copy of gold. Pipeline configuration goes in
with `--env GOLD_MODE=full`. Results: `.ccsim/bench/lake/<label>-<days>d.{json,md}` (and `.partial.json` every
10 batches, for runs that get killed).

`layout_bench` rewrites one gold export as variants (file split, codec, sort order, row-group size) and serves
each from a plain ranged-HTTP server that counts requests and bytes, so the numbers show what the Parquet layout
makes DuckDB fetch, without the simulator's SigV4/AES/IAM overhead.

The local S3 endpoint is one Python process (AES-GCM, GIL), so its wall-clock is not S3's. The portable numbers
are requests and bytes; both tools also print an S3 estimate, requests x 25 ms / parallelism + bytes / 100 MB/s.
Findings and the recommended layout: `docs/data-backend/lake-optimization.md`.
