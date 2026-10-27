# cc CLI + SQL guard

[Overview](00-overview.md) › [Analyzer](40-analyzer.md) › cc CLI + SQL guard

Every query from a preset or the agent passes the same checks before it reaches the reader session, which can see
only one customer's data. The lossless tables open only when the agent states why gold cannot answer, and that
reason is masked, logged and shown to the user.

![cc CLI + SQL guard](../png/44-sql-guard-escalation.png)

## In this diagram

| Item | What it does (one sentence) | Details |
|---|---|---|
| Preset KPI queries | Send fixed SQL with bound values. | [Preset KPI queries](42-standard-kpi-path.md) |
| Claude Haiku · Strands agent | Sends the agent's SQL, or an escalation. | [Claim Query Agent](43-custom-analysis-agent.md) |
| One read-only SELECT? | Refuses anything but a single SELECT. | |
| Allowed tables and functions? | Allows only `sem.*` (and `lossless.*` after escalation) and refuses functions that reach files, the network or settings. | |
| Join cost OK? | Refuses a join without a key over 100 million row pairs, before it runs. | |
| Query time left? | Stops a query at 25 s and the question at 60 s of total query time. | |
| DuckDB reader session | Runs the query read-only, caps the rows and redacts identifier columns. | [Token to credentials](22-token-to-credentials.md) |
| IAM + KMS gate | AWS allows the reader role to read only this customer's tables (gate 6). | [Customer isolation](23-customer-isolation.md) |
| `sem.*` · semantic layer | The gold tables, read by default. | [S3 layout](12-s3-layout.md) |
| `lossless.*` · EDI tables | PHI-stripped EDI tables published next to gold, read only after escalation. | [Gold publish](15-gold-publish.md) |
| escalate · reason | The agent's request for lossless, with its reason. | |
| Reason masked, logged, shown to the user | Masks the reason, records it and re-opens the session with `lossless.*`. | |

## How it works

1. A query arrives from a preset or the agent.
2. The guard checks it is one read-only SELECT over allowed tables, with no banned functions.
3. It reads the query plan and refuses a keyless join that is too large, then runs the query with a time limit.
4. The reader session reads the customer's `sem.*`. Results come back capped, with identifier columns redacted.
5. To escalate, the agent sends a reason. It is masked, logged and shown as a step, and the session is re-opened for
   the same customer with `lossless.*` mounted. The guard then allows `lossless.*`.
6. Any refused check returns an error to the caller and nothing runs.

## Specification

| Item | Value |
|---|---|
| Statement | Exactly one; SELECT, UNION, INTERSECT or EXCEPT; no recursive CTEs; at most 32,768 characters |
| Tables | Schema-qualified `sem.*`, or `lossless.*` after escalation; no table functions, no catalog names |
| Banned functions | File readers (`read_*`, `parquet_*`, `glob` …), database scanners, `http_*`, secrets and settings, extension loading, `query`, `eval`, `shell`, `system` |
| Join cost | Keyless joins over 100,000,000 row pairs refused before they run |
| Time | 25 s per query; 60 s total query time per question |
| Rows | 1,000 per query (up to 10,000 if configured); 20 shown to the model; 50 in the answer preview, full result as a CSV download |
| Redaction | Patient and member names and ids, birth dates, addresses, SSNs, bank accounts, raw EDI text and file names |
| Reader session | One DuckDB session per run with the customer's reader role for 15 minutes; it holds only the mounted tables, cannot read other files or the network, and its settings are locked |
| Escalation | Only the agent can escalate, and only with a reason. The reason is put on one line, masked (quotes, dates, e-mails, ids, SSN- and phone-like values) and cut to 300 characters. Same customer only. |
| Keys | The customer's lake key for the tables; reads are allowed by the customer's reader role (gate 6) |
| Audit | Each query: customer, user, gold version, ok or failed, rows, seconds, error and the first 500 characters of the SQL, stored under the audit key. Escalations: requested, granted or failed. Server logs hold no SQL or rows. |

## Related

Up: [Analyzer](40-analyzer.md) · See also: [Customer isolation](23-customer-isolation.md) · [Audit and logging](53-audit-logging.md)
