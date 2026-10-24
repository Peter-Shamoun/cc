<!-- STATUS: CURRENT (design) - latest agreed data model, not yet loaded (parser not built). Partly outdated: header says only remit_event + line_match get materialized; superseded by the data-backend plan (every sem.* view materialized to semantic_layer/ each batch); open: run_semantic_sql only allows sem.*, but the plan lets the agent escalate to the lossless schema (lane 2); not reconciled yet | source: project knowledge file (added 2026-10-22) -->
# ClaimClarity — lossless EDI schema + semantic layer + agent interface

| File | What it is |
|---|---|
| `01_lossless_edi_schema.sql` | 41 tables (28 EDI + 13 reference/config; DuckDB/Parquet): provenance incl. S3 location, byte-level segment spine, envelopes, shared children, 837P loops, 835 loops, code sets/config |
| `02_semantic_layer_views.sql` | 10 public `sem.*` views (one grain each) + `sem_int.*` plumbing the agent never sees |
| `03_semantic_model.yaml` | 36 metrics, 48 dimensions, glossary, 13 verified requests |
| `04_agent_interface.py` | Compiler + 5 tools (Anthropic tool schemas included), SQL guard for the escape hatch |
| `05_agent_system_prompt.md` | Prompt section that binds the agent to the semantic layer |
| `tests/test_semantic_layer.py` | Synthetic 837P/835 data with denials, reversals, corrections, $0 non-denials, resubmissions; asserts results equal independently computed ground truth |

## What the research says, and how it shaped this

**835 / 837P facts the schema is built around**
- A PCN is not a key. One CLM01/CLP01 legitimately appears many times: resubmissions (frequency 7/8), secondary payers, and reversal/correction pairs. Per the TR3, a reversal (CLP02=22) must negate every line and every original CAS, then the correction is sent; when both are in one transaction the reversal comes first. Hence deterministic `file_id:seg_ord` surrogate keys, and "current adjudication" computed by event order, not by uniqueness.
- SVC01 is the *adjudicated* procedure; SVC06 carries the *submitted* procedure only when the payer changed it. `procedure_code` = SVC06 else SVC01, so CPT rates group by what the lab billed.
- REF*6R is returned only if it was sent on the 837 (or the payer split lines), so 837↔835 line matching falls back to procedure+modifier+charge, then procedure + line order.
- 835 balancing: BPR02 = ΣCLP04 − ΣPLB; claim charge = paid + Σ adjustments. `sem.payments.is_balanced` surfaces violations as a data-quality signal.
- CLP05 equals the PR-group adjustments except on reversals; PLB carries non-claim money (recoupments WO/72, interest L6, forward balances FB).

**Agent ↔ database interface**
- dbt Labs' April 2026 rerun of the data.world ACME Insurance benchmark: Claude Sonnet 4.6 scored 90.0% with text-to-SQL vs 98.2% through a modeled semantic layer (GPT-5.3-Codex: 84.1% vs 100%). Raw text-to-SQL over the un-modeled schema was 64.5%. Out-of-scope questions score 0% via the semantic layer — by design it refuses rather than guesses.
- Spider 2.0 (ICLR 2025): enterprise-shaped schemas drop frontier models from ~91% (Spider 1.0) to ~21%; schema/column linking is the largest error class.
- Snowflake Cortex Analyst's pattern of a YAML semantic model plus a *verified query repository* is the model for `verified_requests`.
- Takeaway: the model should choose *metrics and dimensions by name*; a compiler should own joins, grain, and business rules; a guarded SQL escape hatch handles the long tail.

## The CPT question, end to end
> Which CPT codes have the highest denial rate, among codes with at least 20 remitted claims?

The agent sends:
```json
{"metrics": ["denial_rate","denied_claims","remitted_claims"], "group_by": ["procedure_code"],
 "min_group_size": 20, "order_by": [{"field":"denial_rate","direction":"desc"}], "limit": 15}
```
The compiler sees `procedure_code` isn't on `sem.remit_claims`, picks `sem.remit_lines`, applies the default `is_current_adjudication` filter, counts distinct claim+payer (not lines), and drops groups under 20. In the test this matches hand-computed truth for every code.

## Design decisions worth arguing about
1. **Two-level losslessness.** `edi.segment` (raw text + element list + delimiters) reproduces files byte-for-byte; typed tables are lossless at the element level and every row points back to its `seg_ord`. Low-volume segments (PWK, CR*, CN1, HCP, MEA…) land in `edi.misc_segment` instead of 15 more tables.
2. **Shared children** (`entity`, `reference`, `date_time`, `amount`, `quantity`, `note`, `contact`, `adjustment`) keyed by `owner_uid + owner_table`. CAS is exploded to one row per triplet — this is what makes CARC analysis a one-table question.
3. **Tenant isolation is physical** (bucket/prefix per customer); no tenant column on data tables.
4. **Business rules live in views + `ref.*` config**, not in the stored data: the denial CARC list (`ref.denial_carc`), whether status 4 counts (`ref.tenant_setting`), and payer resolution (`ref.payer_alias`) can change without reloading.
5. **PHI minimization**: `sem` exposes claim keys and NPIs but no patient names, member IDs, DOBs, or bank accounts.
6. **The agent never picks a view.** Metrics declare which views can compute them; the compiler picks the coarsest one that has every requested field and returns a model-readable error otherwise.

## Fit with what already exists
- `query_metrics` is the typed-task path (server compiles SQL; no model-written SQL on the live path). `run_semantic_sql` is a lane-2 (ADR-009) style escape hatch, but scoped to `sem.*` and parsed with sqlglot.
- The denial definitions are the existing ones (recorded = CLP02 4; signal v3 = non-reversal, $0 paid, CO CAS in the reviewed CARC list). **`ref.denial_carc` must be loaded with the 23 reviewed CARCs** — the test uses placeholders.
- The dedupe in `sem_int.remit_event` (file-level `duplicate_of_file_id` / `superseded_by_file_id` + "latest event per PCN+payer") is the fix for the `keys.remittances` duplicate-key failure.

## Known gaps / next steps
- **Parser not included**: the DDL defines the target; the X12 → Parquet loader still needs to populate it (including `loop_path`, the copied REF*6R columns, and `edi.segment`).
- **Materialize** `sem_int.remit_event` and `sem_int.line_match` at load time for large tenants.
- **Payer matching** between 837 2010BB and 835 1000A relies on `ref.payer_alias`; until populated, payer_key is a normalized name, so the same payer can appear twice if names differ across sides.
- **Line matching** is heuristic when REF*6R is missing; `remit_match_method` records which tier matched.
- **Time-range presets** anchor to today by default; historical conversion files should use `anchor: data`.
- **Not modeled**: 277CA/999 rejections (not in 835/837), 2320/2330 COB detail in `sem` (it is in the lossless layer).
