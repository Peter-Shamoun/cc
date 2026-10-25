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

## Glossary protocol (2026-10-25)

The glossary is a trained artifact. Train failures may be read and turned into general entries. Validation is never read; it only accepts or rejects a change. Test is sealed and run once, with `--unseal-test`.

Near-duplicates share a split: same parametric family, a declared template bundle, an explicit `template`, the same normalized signature (numbers, dates, months, CPT/HCPCS, placeholders masked), an exclusive content 8-gram in any author round, or token Jaccard ≥ 0.72. Author round is a stratum (50/25/25 inside the round), not a leak. A template that crosses rounds is one group in the `mixed` stratum. Algorithm `splits-v1`, seed `claimclarity-glossary-splits-v1`, file `bench/question_splits.json`, assignment hash `7f7600eaaaaafe6ee5fdd6c0c63644fd12886973ae6152dec15be2746e4de10d`.

Concrete questions: **49 train / 25 validation / 25 test** (99). With the 16 family templates: 57 / 29 / 29, 92 groups. Largest groups first, so the denial-rate bundle (8) is train, denial-CARC (5) is validation, and partial line denial (3) is test. The historical `heldout_hard_1` set is redistributed across the three splits; it is not kept intact as validation. Adding a question (`python -m bench.splits sync`, the round-3 path) does not move a frozen row. An unlinked question goes to validation. Linking two frozen splits raises `SplitConflict`.

`v000_basic` and `v001_screen1_pitfalls` are the sandbox strings, byte-identical, under `bench/glossaries/`. v001 is a pre-split artifact. The contamination audit (`research/glossary_contamination.md`) flags nothing: 0 question-specific bullets. Mean IDF recall, screen1 − heldout, is −0.037 for the basic glossary and −0.023 for pitfalls. Pitfalls are not more screen-specific than the basic definitions. The Haiku gain that failed to replicate is a coverage miss, not a copied question. Two shared 5-grams (adjudication / denial-rate wording) match many questions and are listed, not flagged. No numeric answer leaks into an interpretation.

The learner (`bench/glossary_learner.py`) proposes from train failures with a scripted error-type map or an injected `complete(prompt)`. It does not open a paid client. Validation crosses the boundary only as `{effect, ci95, questions}`. A candidate is accepted when the effect is ≥ 0.05 or the CI lower bound is above 0 (either, any model). Effect-only accepts are provisional until validation is large enough for the CI. Accepted entries become a new version file whose provenance names the parent and the train failure ids.

Power (`research/screen2_power.md`) recovers the paired SD from the held-out bootstrap width, because the per-question rows were not kept. At 2 repeats the conservative (noisiest) numbers are: Haiku SD 41.8pp, n for 5pp at 80% = 548, n for 10pp at 80% = 137, n for a 10pp CI that clears 0 = 67. Sonnet SD 22.1pp, n = 154 / 39 / 19. Going from 2 to 8 repeats only moves Haiku's 10pp 80% n from 137 to 116. Round 3 (~56) onto the 25 validation questions makes 81: enough to see a 10-point Haiku effect on the CI bar and a 10-point Sonnet effect at 80% power, not enough for 80% power on Haiku 10 points. Do not unseal test to buy power. Repeats stay at 2.

Screen 2 (`research/screen2.yaml`, `python -m bench.screen2 plan`) is coordinate ascent on validation around the screen-1 winner. Glossary text is learned on train. Examples come from train. Cascade and multi-candidate are orchestrator scores: Haiku's answer stands when Haiku submitted one, otherwise Sonnet; majority of candidates, not pass@k.

## Backlog (top = next)
- [done] Agent-SQL security stress (tests/test_security_stress.py, docs/security/stress-test.md): guard fuzz,
  cross-tenant, Safe Harbor column scan, prompt-injection boundaries, hash-chained audit. Generator stays 3.3.
- [done] Glossary-learning infrastructure: split manager, contamination audit, learner scaffold, power analysis, screen-2 spec. See "Glossary protocol" above. Generator, parser, and lake were not changed.
- [next] When round 3 lands, `python -m bench.splits sync` (unlinked questions go to validation; do not rewrite the freeze) and re-run `python -m bench.glossary_contamination` before those questions gate a glossary.
- [next] Run the learner on train failures only. Gate each candidate on a validation paired bootstrap. Treat an accept that clears the 5-point effect but not the CI as provisional until validation is near the ~67-question CI bar (25 + ~56 = 81 gets there).
- [next] Screen 2 on validation, one factor at a time, 2 repeats: glossary version, semantic layer, train-only examples, majority of 3 candidates, Haiku-then-Sonnet cascade. Leave the test split sealed until the single end-of-protocol run.
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
- [done, branch cursor/analyzer-v1-9e95] analyzer v1 (`analyzer/`, `docs/agent/README.md`): tool layer over ccsim.query (guard + audited escalation), repair/verify loop, screen-factor flags, candidate vote, Haiku-then-Sonnet cascade, scripted LLM, `eval_subagents --agent analyzer`. Generator stays 3.3; no paid API calls.
- [next] harder question set (Sonnet at ceiling): Opus authors multi-step questions; then screen 2 =
  coordinate ascent around the winner (glossary content variants, examples/semantic layer on hard questions,
  model cascade Haiku->Sonnet, multi-candidate) with repeats for pass^k
- [done] held-out validation (heldout_hard_1, 27 questions): the screen-1 pitfalls gain did not replicate
  (research/heldout_results_1.md). The per-question rows were not kept; power uses the published intervals.
- [superseded] screen 2 as an unsplit coordinate ascent on heldout_hard_1. The protocol above replaces it:
  learn on train, gate on validation, test sealed. Round 3 stays parked until the verifiers pass; do not wait
  on it to use the split manager.
- [later] Expand families further after calibration lands (target 150+ at full scale)
- [later] Ambiguity / abstention / multi-turn splits (T20-T22)
- [later] Screening experiment, then adaptive search over technique permutations
