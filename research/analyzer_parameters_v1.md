# Analyzer parameter space v1

The 26 parameters the analyzer search draws from. Sources: `research/technique_factors.yaml` (T01–T27),
`research/sota_refresh_2.md`, our own screen 1 / held-out / grid results, and five additions (#22–#26).
"Off" is a level of every parameter. Multiplying the levels below gives about 1.86e13 combinations, so the search
tests research-designed bundles, takes the winner apart (leave-one-out), then adds unused parameters one at a time.

Evidence columns: "SOTA" is from other people's benchmarks (other models, other data); "ours" is screen 1 and the
held-out check on Haiku/Sonnet, or the grid (Grok as the analyst, DEV_HARD_v1, near ceiling: weak evidence).

## A. What the model knows
| # | Parameter | Levels | SOTA | Ours |
|---|---|---|---|---|
| 1 | Glossary / domain rules | none · basic · + invariants · + pitfalls | +17 to +23 pp (BIRD evidence, Cube, Rumiantsau 2026) | pitfalls +15.6 Haiku in screen 1, −1.9 held-out (overfit); Sonnet +7.4 held |
| 2 | Learned playbook | off · bullets distilled from TRAIN only, human-reviewed, frozen | ACE; NVIDIA DABstep | untested |
| 3 | Data dictionary | types only · + descriptions/grain · + derivations | DAIL-SQL, Pinterest | bundled in screen-1 winner (+11 Haiku held-out, n.s.) |
| 4 | Data profile | none · enums · full profile | profiling-only #1 on BIRD (2025) | same as #3 |
| 5 | Value lookup tool | off · on · mandatory before literals | CHASE −2.9 without | screen 1 ≈ 0 |
| 6 | Dynamic few-shot examples | k 0/3/5 · lexical/embedding selector | DAIL-SQL +3 to +10 | screen 1 ≈ 0; grid +0.1 |
| 7 | Prompt style | procedural · goal · minimal | OpenAI: prescriptive hurts | procedural +4.5 Haiku in screen 1, gone held-out |

## B. How the answer is produced
| # | Parameter | Levels | SOTA | Ours |
|---|---|---|---|---|
| 8 | Generation mode | free SQL · typed metric compiler · decompose into CTE steps | dbt 2026 90→98% | grid: metric_spec +1.0, decompose −0.8 |
| 9 | Explore data first | off · probe before answering | ReFoRCE ≈ −2 without | grid +0.6 |
| 10 | Plan first | none · structured plan · plan + replan on failure | CHASE +6; MAC-SQL +3.9 | untested |
| 11 | State assumptions (grain, date basis, population) | off · on | — | grid +0.8; with fewshot +9.2 (1 of ~30 pairs) |
| 12 | Reasoning effort | low · medium · high · low then high on failures | dbt: no change | screen 1: −2.4 Haiku / +1.7 Sonnet |

## C. Checking and fixing
| # | Parameter | Levels | SOTA | Ours |
|---|---|---|---|---|
| 13 | Execution repair | none · errors only · + hints · + result signals; max 1/2/4 | +1 to +4.4 pp, cheap, most consistent | grid +2.1 (largest seen) |
| 14 | Programmatic result checks | off · rules (zero rows, rate outside [0,1], fan-out) · + Haiku verifier | OpenAI, ReFoRCE, DS-STAR +5.3 | screen 1 ≈ 0; grid verifier −0.8 |
| 15 | Multiple candidates + selection | 1 · 3 vote · 3 + selector · 3 agree-or-abstain | CHASE +4 to +10; self-consistency ≈ 0 at 5× | grid +1.5. No temperature on the API: diversity must come from prompts/models |
| 16 | Abstention | prompt only · agree-or-abstain · verifier veto | EHRSQL | untested; needs unanswerable questions |

## D. Architecture and cost
| # | Parameter | Levels | SOTA | Ours |
|---|---|---|---|---|
| 17 | Model tier | Haiku · Sonnet · Haiku then Sonnet on a failure signal | EllieSQL −40% tokens, equal accuracy | untested |
| 18 | Multi-agent | single · + verifier context · Sonnet planner → Haiku executor | Anthropic ≈15× tokens | grid −0.8 |
| 19 | Tool design | raw/actionable errors · full/digest results · descriptions | Anthropic tool guide | untested |
| 20 | Loop budgets | 8/4 turns · 50/20 rows to the model | — | fixed |
| 21 | Clarification / multi-turn state | never ask · ask on listed ambiguities · ask when uncertain; transcript vs plan state | BIRD-Interact | needs ambiguous questions + user simulator |

## E. Additions (ours)
| # | Parameter | Idea |
|---|---|---|
| 22 | Domain SQL lint | rule checks for our traps (missing is_current_adjudication / is_latest_submission, wrong date column, grain-multiplying joins); feeds repair |
| 23 | Reconciliation check | the answer must agree with totals that have to agree (835 claim payments vs payment totals, rate parts vs denominator) |
| 24 | Answer by two routes | e.g. free SQL and the metric compiler; answer only when they agree (diversity without temperature) |
| 25 | Restate the question | canonical measure / grain / filters / window before SQL, kept as an audit record |
| 26 | Escalation trigger | what sends Haiku to Sonnet in #17: lint hit, failed repair, candidate disagreement, low self-rated confidence |

Fixed, not searched: prompt caching, PHI/injection guards in code, no schema pruning (ten `sem.*` views fit).
