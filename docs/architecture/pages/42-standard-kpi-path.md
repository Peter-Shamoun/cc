# Standard KPI path

[Overview](00-overview.md) › [Agent analyzer](40-analyzer.md) › Standard KPI path

When the KPI gate finds that one template fits, the answer comes from reviewed, fixed SQL instead of SQL the model
writes. The values are bound as parameters, so the model cannot change the query. Code formats the numbers, and one
short model call turns them into a sentence. The same KPI asked twice is computed the same way every time.

![Standard KPI path](../png/42-standard-kpi-path.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Gate decision | A template id and values that passed the server check | [KPI gate](41-kpi-gate.md) |
| 2 | Template SQL | The template's fixed SQL from the catalog, with DuckDB named parameters | [KPI gate](41-kpi-gate.md) |
| 3 | Guarded reader | The same SQL guard, time limit and read-only reader session as the custom analysis path | [SQL guard and escalation](44-sql-guard-escalation.md) |
| 4 | Customer B's gold | The `sem.*` views of the current gold version | [Gold publish](15-gold-publish.md) |
| 5 | Number formatter | Formats rates, money and counts in code | |
| 6 | Reply writer | One model call with no tools. It writes 1–3 sentences from the formatted rows. | [Model calls](45-model-calls.md) |
| 7 | Amazon Bedrock | Claude Haiku | [Amazon Bedrock card](90-service-catalog.md) |
| 8 | Answer | The same shape as a custom analysis answer, marked `via="template:<id>@v1"` | [Custom analysis agent](43-custom-analysis-agent.md) |
| 9 | Display copy | The same SQL with the values written in as literals, for people to read | |

## How it works

1. The checked gate decision (1) selects one template (2). Its values become bound parameters (`$payer`,
   `$payer_class`, `$month`, `$cpt`, `$carc`). No value is ever pasted into the SQL that runs.
2. The SQL runs through the guarded reader (3) on the customer's gold (4): the same guard, the same 25 s per query
   and 60 s per question, the same read-only session and the same tool layer as the custom analysis path.
3. The number formatter (5) formats the result in code:
   - rates and shares as a percentage with one decimal (12.3%);
   - money as $1,234.56;
   - counts with thousands separators (1,649).
4. The reply writer (6) makes one call to Claude Haiku on Bedrock (7). It gets the question, the formatted rows
   and the gate's assumptions. It copies the values exactly, states the assumptions and writes no table.
5. The answer (8) has the text, the result table, the SQL and the assumptions, in the same parts as a custom
   analysis answer. It is marked `via="template:<id>@v1"`.
6. The display copy (9) is used everywhere SQL leaves the executor: the SQL panel in the answer, the conversation
   history, the run events and exports. It runs as-is to the same rows, so a reader can copy and check it.

## Specification

| Item | Value |
|---|---|
| SQL that runs | The template's fixed SQL with bound parameters. Never model-written. |
| SQL that is shown | The display copy, with values inlined as SQL literals. The standard literal-masking policy for shown SQL still applies. |
| Data read | Gold (`sem.*`) only. The standard KPI path never escalates to lossless. |
| Permissions | The run worker's reader session, with the customer's temporary role credentials (gate 6: IAM + KMS allow the reader) |
| Limits | 25 s per query, 60 s total query time per question, 1,000 rows, as on [SQL guard and escalation](44-sql-guard-escalation.md) |
| Model calls | One reply-writer call (the gate call comes before). No tools. |
| Latency | Reply writer 1–2 s. A whole standard KPI turn, gate included, about 4 s. |
| Cost | About $0.004 per standard KPI turn |
| Numbers in the reply | Copied from the formatted rows. The table in the answer comes from the query, not from the model. |
| PHI | Result rows are customer data. Identifier columns are redacted by the tool layer, as on every query. |

**Failure behaviour.** A SQL error, a timeout or a failed reply on this path is logged and the turn moves to the
custom analysis path. The user sees one answer, never a standard-path error.

**Logging.** Each run records the template id, the template version and the bound values (payer names, months and
codes; no patient data), plus the query's audit record: ok or failed, row count and seconds.

## Related

Up: [Agent analyzer](40-analyzer.md) · See also: [KPI gate](41-kpi-gate.md) ·
[SQL guard and escalation](44-sql-guard-escalation.md) · [Model calls](45-model-calls.md)
