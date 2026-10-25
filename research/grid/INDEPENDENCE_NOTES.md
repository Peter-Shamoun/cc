# Grid cell independence notes (DEV_HARD_v1 and v1 easy set)

Written by a Grok Bot local executor on 2026-10-25 (PT), branch `grokbot/dev-hard-v2`.
Source: grep of `research/grid/*.json` (`agent_note`, `notes`) and `research/grid/*.md` on
`claude/trusting-heisenberg-8xvazk` at merge `8ffbc42` (PR #58). Only the cells' own notes were read. No gold
answers, reference SQL, question families or glossary were opened.

## Why this matters

Several runners did 3–5 cells one after another in a single agent context. Later cells in a block started from
the earlier cells' SQL, and some runners had already seen the earlier cells' per-question pass/fail before
drafting. Those cells are not independent samples of their config. Treating them as independent makes the
factor effects look more certain than they are, and it biases them toward zero: the cells "converge" because
they share drafts, not because the factor did nothing.

## Classification

Tier | Meaning | Recommended weight
---|---|---
**A0 anchor** | First cell in a sequential block. Drafted from scratch in that context | 1.0
**A explicit reuse** | Cell's own note or .md says it reused, carried over, or started from an earlier cell's SQL/drafts, or its answers are identical to an earlier cell | **0 in the primary analysis** (drop). 0.25 in a sensitivity run
**B sequential, no explicit SQL reuse** | Same agent ran earlier cells and knew their outcomes, but the note doesn't say SQL was copied | 0.5
**C contamination flag** | Agent saw glossary/family templates or harness SQL earlier | 0.5 (multiply with tier weight), and report with/without

### Tier A: explicit SQL or draft reuse (drop or downweight)

Block (executor) | Anchor (keep) | Reusing cells | Evidence (paraphrased from the cell's notes)
---|---|---|---
b0 | H017 | **H018, H019, H020** | H018 "reuses H017's metric-spec SQL". H019 and H020: "Answers are identical to H018"; same analyst saw H017–H019 pass/fail
b1 | H021 | **H022, H023, H024** | "same executor and context … the SQL carried over"; "correlated with H021, not independent"
b2 | H025 | **H026, H027, H028** | "NOT independent of H025: drafts were reused and the two DuckDB binder-error fixes were known"
b3 | H029 | **H030, H031, H032** | "H029 final SQL served as the starting draft"; "contaminated by carryover"; same SQL as H029 on q11
b4 | H033 | **H034, H035, H036** | "run back to back in a single analyst context … later cells reuse the earlier SQL"; "remaining SQL reused from H033"
b5 | H037 | **H038, H039, H040** | "Drafts reuse the executor's H037 SQL"; H040 "least independent of the batch"
l0 | H065 | **H066, H067, H068, H069** | "Single executor l0 ran all 5 cells sequentially with one shared draft pool; cells differ only where factor logic changes the selected SQL"

### Tier B: sequential, same agent, outcome knowledge (weight 0.5)

Agent | Cells | Note
---|---|---
cloud agent after C005 | H002, H003, H004 | "sequential, same agent as C005". H003/H004 notes discuss H002's misses, so the agent knew earlier outcomes. Treat H002 as anchor (1.0), H003–H004 at 0.5
cloud agent after C001 | H008, H009, H010 | "sequential, same agent as C001". H008 anchor, H009–H010 at 0.5
cloud agent after C010 | H062, H063, H064 | "same agent as C010; saw some harness SQL earlier". H062 anchor; H063–H064 at 0.5. Also tier C
v1 easy set | C012–C014, C015–C017, C027–C029 | Agents reused across cells (`agent_note` "reused … builder/agent"). C012–C014 notes say the three cells "converged on the same SQL". All v1 cells are at ceiling (19–20/20) anyway, so they carry almost no factor information

### Tier C: contamination flags

Cells | Flag
---|---
H000, H001 | DEV_HARD builder; "saw glossary/family templates earlier"
H062, H063, H064 | "saw some harness SQL earlier"
H065–H069 | l0 disclosed glancing at H001's aggregate accuracy line (no per-question data)

### Not yet classifiable

H073 and H083 (executors l1 and l3) had landed without scores or reuse notes at the time of writing. The other
l1–l6 cells were still running. Because each l-executor runs 5 cells in one context, **assume the 2nd–5th cell of
every l-block is tier A** unless its notes clearly show a fresh draft. The aggregator should re-run the grep
below after the last PRs merge.

## Recommended handling in `aggregate.py`

1. **Primary analysis:** keep anchors and fully independent cells only (tier A weight 0). This leaves roughly one
   cell per sequential block. That is thin, which is the honest state of the evidence.
2. **Sensitivity analysis:** tier A 0.25, tier B 0.5, tier C x0.5. Report whether any factor's sign flips
   between primary and sensitivity.
3. **Better:** treat each executor context as a cluster and use cluster-robust standard errors, or a
   mixed model with a random intercept per context. Within-cluster differences between configs are
   *not* clean factor effects, since only the factor-specific passes changed the SQL.
4. Going forward, run one fresh agent context per cell. See `DEV_HARD_v2_PROPOSAL.md`.

Re-check command:

```bash
grep -il -E "reus|carried over|carryover|not independent|starting draft|same sql|shared draft|identical to H" research/grid/[HC]*.json research/grid/[HC]*.md
```
