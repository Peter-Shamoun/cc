# CC work plan (living)

**Standing instruction from the user (2026-10-25): keep working within this scope until the user says stop.
Never decide the work is done; when an item finishes, pick the next highest-value item in scope and loop.**

## Scope (in order of dependency)
1. Mock EDI: realistic 837P/835 at 5 MB/day/customer; 2 customers x 1 year + 1 customer x 3 years.
   "So believable it is nearly real." Generator + validator + ledger.
2. Lake: most efficient Parquet + DuckDB medallion layout; keep medallion structure and schemas; stress test
   loading and querying at that scale.
3. Bench: expanded question set; correct answers derived by Opus and verified against the generator ledger
   (or dual-Opus agreement when no ledger answer exists).
4. Technique search: SOTA catalog (research/sota-techniques-catalog-2026.md, research/technique_factors.yaml)
   -> factors; evaluate permutations with Haiku and Sonnet subagents in a sandbox (Opus only derives answers,
   never evaluated as a production model); adaptive search (screening -> fractional factorial -> local search /
   coordinate ascent around the best configs) until improvements stop exceeding repeat noise, then widen.

## Loop discipline
- Builder agents in worktrees; an independent reviewer per deliverable; fix; re-review until clean.
- Every deliverable: tests green (`pytest -q tests/`), bench selftest sound, pushed checkpoint.
- Disk: check `df -h /` before big runs; delete SFTP/raw copies after ingest; ~27 GB free.

## Backlog (top = next)
- [running] Calibrate generator to research/edi_realism_calibration.yaml (worktree agent)
- [running] Lake baseline + optimization (worktree agent)
- [next] Independent realism audit of calibrated data (stats vs spec, X12 vs TR3, "would a biller believe it")
- [next] Generate full datasets (2x1y, 1x3y) through the winning lake; record sizes/timings
- [next] Phase-4 sandbox: bench/agent_cli.py (logged CLI = the only tools an evaluated subagent may use)
- [later] Expand bench to 150+ questions (templated families x parameters, ledger-verified; Opus-derived for the rest)
- [later] Ambiguity / abstention / multi-turn splits (T20-T22)
- [later] Screening experiment, then adaptive search over technique permutations
