# Glossary

[Overview](00-overview.md) › Glossary

| Term | Meaning |
|---|---|
| 837P | The X12 professional claim a lab sends to a payer |
| 835 | The X12 remittance advice: a payer's payment or denial for a claim |
| 277 | The X12 claim status response |
| CARC | Claim adjustment reason code on an 835 (e.g. CO-16: missing information). Group CO is a contractual obligation, PR is patient responsibility. |
| Payer / payer class | The insurer. Payer class groups payers: commercial, Medicare, Medicare Advantage, Medicaid, Medicaid MCO, Blues, other. |
| Current adjudication | A payer's latest decision on a claim, after reversals and corrections. Most KPIs count only these. |
| Reversal | An 835 with claim status 22, which takes back an earlier payment |
| Raw (bronze) | The files exactly as received. Written once, never changed (Object Lock). |
| Lossless (silver) | Parsed tables that keep every segment and can point back to the byte offsets in the raw file |
| Semantic (gold), `sem.*` | Curated, business-ready views (claims, remit claims, remit lines, adjustments). The chat reads these. |
| Escalation | The analyzer reading the PHI-stripped `lossless.*` tables published next to gold (never silver objects). Allowed only with a stated reason, which is recorded. |
| Version, CURRENT pointer | Each parser run publishes a new gold version. CURRENT names the version readers use. It is updated with compare-and-set, and old versions stay readable for sessions already open. |
| Batch lease | A per-customer lock held by one parser task at a time, fenced, so two tasks never write the same customer's lake |
| KPI gate | The first model call of a turn. It decides whether a preset KPI query answers the question exactly. |
| Preset KPI query (standard KPI path) | One of the 16 fixed, reviewed SQL queries. The gate fills in its values (payer, month, CPT, CARC, payer class). |
| Claim Query Agent (custom analysis path) | The Strands agent in the analyzer that writes its own read-only SQL over gold, through the cc CLI and SQL guard |
| SQL guard | The check every analyzer query passes: read-only, banned functions, join-cost limit, 60 s total query time per question |
| Reader session | The per-run DuckDB session opened with the customer's temporary role credentials. It can read only that customer's current gold version. |
| Run | One question being answered. It has an id, a budget and a stream of numbered events. |
| Run events | The steps, SQL, rows and answer of a run, stored in numbered chunks so a reconnecting browser can replay them |
| SSE | Server-sent events: how the browser receives a run's events as they happen |
| Prompt caching | Bedrock reuses the unchanged start of a prompt (instructions, schema, tools) across calls, which cuts cost and latency |
| Gates 1–6 | The AWS checks on the write path (1 EventBridge rule, 2 PassRole, 3 IAM + KMS) and the read path (4 token, 5 trust policy, 6 IAM + KMS) |
| PHI | Protected health information. Everything under a customer's prefix is treated as PHI. |
