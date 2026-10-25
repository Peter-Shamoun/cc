# ClaimClarity work plan (living)

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
- [done] Agent-SQL security stress (tests/test_security_stress.py, docs/security/stress-test.md): guard fuzz,
  cross-tenant, Safe Harbor column scan, prompt-injection boundaries, hash-chained audit. Generator stays 3.3.
- [running] Calibrate generator to research/edi_realism_calibration.yaml (worktree agent)
- [running] Lake baseline + optimization (worktree agent)
- [next] Independent realism audit of calibrated data (stats vs spec, X12 vs TR3, "would a biller believe it")
- [next] Generate full datasets (2x1y, 1x3y) through the winning lake; record sizes/timings
- [done] Phase-4 sandbox: bench/agent_cli.py + agent_session.py + eval_subagents.py; pilot 12 Haiku runs OK
- [done] Answer-key DB (bench/truth_db.py) + 16 question families (bench/question_families.py): 99 questions sound on a 45-day sample
- [done] Opus authoring pipeline (bench/authored_questions.py): author gold+answer-key SQL, independent Opus verifier,
  accept on three-way agreement + non-trivial; prototype 4/4 accepted, selftest sound (103 questions)
- [done] scaled authoring: 35/35 accepted (39 authored total); 5 answer keys later fixed on generator 3.0 data
  (payer class from adjudications, re-original double count, billed vs paid units)
- [done] generator 3.0 (calibration engineer) merged; duplicate-claim denials no longer replace the original
  adjudication (views, generator ledger, answer key agree)
- [done] realism audit 1 (independent): 5/10, research/edi_realism_audit_1.md
- [running] realism engineer on audit-1 findings (worktree); then re-audit, loop until >= 8/10
- [done] Phase 4 screen 1 (research/screening_results_1.md): pitfalls glossary +16 pts Haiku / +6 Sonnet,
  procedural steps +5 Haiku; others ~0. Haiku+pitfalls ~= Sonnet. Matcher false-reject fixed during scoring.
- [done] generator 3.1 merged (audit-1 fixes); audit 2 = 7/10 (research/edi_realism_audit_2.md); realism
  engineer on audit-2 findings (target >= 8.5)
- [done] lake optimization merged (incremental gold, manifests, quarter folds) + review fixes (unique keys,
  manifest check, month-close crash rebuild, fenced lock, reader retention, per-relation reader scope); 227 green
- [done] PHI: default-deny allowlist of REF qualifiers in lossless.edi_reference
- OWNER 2026-10-25 late: too much usage on realism; close prerequisites with final verdicts, then move to the
  agent analyzer. Generator FROZEN at 3.3 (audit 4 = final verdict, findings = known limitations, no more fix
  rounds); parser + silver agents wrapping up (no new experiments); scale runs are CPU-only, run in background
- (superseded) data groundwork focus, then stop before the agent analyzer
  - [running] realism round 2 (audit-2 findings, target >= 8.5), then re-audit
  - [running] parser loop: speed/memory with byte-identical Parquet (oracle), then edi.segment slimmed to
    bronze byte offsets (raw_text/elements dropped; lossless proven against bronze)
  - [running] silver: open-month small-file compaction, claim-key index, re-measure
  - [running] design check: DuckLake / Iceberg / current, measured + security fit -> lake-design-check.md
  - [next] scale runs 2 customers x 1 year, 1 customer x 3 years through the merged pipeline
  - [decide after parser numbers] manager/worker parse fan-out (owner idea): stage 1 in-task process pool
    (parser loop measures 1/2/4 workers + 3-year backfill projection); build SQS / Distributed Map fan-out
    (+ SQS mock in the sim) only if a 3-year onboarding backfill does not fit one task comfortably (~30 min on
    4 vCPU, well inside the 2 h batch lease). Commit + gold stay single-writer per customer under the lock.
- PARKED until after the groundwork (owner): authoring round 3 ingest (13 verifiers failed on the limit),
  SOTA research refresh, screen 2 / gradient search, then React front end
- [running] held-out validation (heldout_hard_1, 27 questions, 4 configs x Haiku/Sonnet x 2 repeats)
- [next] harder question set (Sonnet at ceiling): Opus authors multi-step questions; then screen 2 =
  coordinate ascent around the winner (glossary content variants, examples/semantic layer on hard questions,
  model cascade Haiku->Sonnet, multi-candidate) with repeats for pass^k
- [later] Expand families further after calibration lands (target 150+ at full scale)
- [later] Ambiguity / abstention / multi-turn splits (T20-T22)
- [later] Screening experiment, then adaptive search over technique permutations
