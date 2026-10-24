## User messages (verbatim)

### Message 1
Wave one of research: become an expert on 835 and 837p files
Wave two of research: become an expert on Conversational Analytics Agents and the best interface for agent interaction with databases, including semantic layers

When done, create 1 schema: the Best possible lossless db schema to store 835 and 837p edi data, along with a table about its s3 location

Then, create the schema for the semantic layer, the views that will point to the lossless schema, and give the agent the best possible interface to interact with the semantic layer so it can answer questions like "Which CPT codes have the highest denial rate, among codes with at least 20 remitted claims?" and 90% of user queries without ever having to visit the full, complex, expensive schema, only digesting the semantic layer

### Message 2
This would be done via parsing .edi files. I have previously heard that it is good common practice to go from raw edi -> parsed semi structured data files -> structured db. Would this still be the case if we have the lossless parquet db? Also, I heard it was best practicve, but WHY is it best practice? doesn't it duplicate data with more overhead, casusing more than double the data?

### Message 3
what is .segment and gold in our case? I dont understand, I thought it was .parquet, and maybe .csv. What are the 3 levels a little more in depth

### Message 4
what are sem views structurally? Files? What do they look like

### Message 5
so views are just queries? .sql? Where are the results to the .sql stored for the agent to query?

### Message 6
So a view is just like a filter pre applied to a query?

Like WITH view_name as (...) select from view_name?

## Notes

### Decided
- The agent talks to a semantic layer through tools, not free SQL. This follows dbt's 2026 benchmark: 98.2% accuracy through a semantic layer vs 90.0% writing SQL directly.
- A patient control number (PCN) is not used as a key. Keys come from file and segment position, and "current state" is decided by the order of 835 events (reversals and corrections).
- `procedure_code` uses SVC06 (the submitted code) when present, otherwise SVC01.
- Layering: raw .edi in S3 (bronze), then `edi.segment` Parquet (silver), then typed Parquet tables (gold), then `sem` views. No separate JSON layer. The parser writes only `edi.segment` plus envelope and source-file rows. Typed tables are built from `edi.segment` in a separate, deterministic step.
- Views are saved queries, not stored results. Only `sem_int.remit_event` and `sem_int.line_match` are candidates for materializing as Parquet.
- Patient names, member IDs and bank details are not exposed in `sem`.

### Built
- Lossless schema: 41 tables (28 EDI + 13 reference/config), including the `edi.source_file` S3 location table.
- 10 `sem.*` views, a semantic model with 36 metrics and 48 dimensions, agent tools (`query_metrics`, `lookup_values`, `get_claim_timeline`, `describe_semantic_layer`, `run_semantic_sql`, `request_lossless_query`), and an agent system prompt.
- A synthetic end-to-end test. It passed, and the CPT denial-rate question matched hand-computed ground truth.
- Bundled as cc_schema.zip. README, 01, 02 and 03 were written to the project under `claude/schema/`.

### Left open
- Load the 23 reviewed CARCs into `ref.denial_carc` (the test uses placeholders).
- Write the X12 parser/loader and run it on the LabCorp files.
- Fill `ref.payer_alias` so that one payer named differently in the 837 and 835 doesn't show up as two payers.
- Offered but not done: a "load order" section in the README stating the segment-first rule.
