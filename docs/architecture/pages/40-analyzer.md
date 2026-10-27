# Agent analyzer

[Overview](00-overview.md) › Agent analyzer

The analyzer turns one question into one answer. It runs inside the run workers (Amazon ECS on AWS Fargate) and
calls Claude Haiku on Amazon Bedrock (every hexagon in the diagram is a model step). Every turn passes a KPI gate first. When one standard KPI query computes
exactly what was asked, fixed SQL answers it. Otherwise the custom analysis agent writes its own read-only SQL over
gold. Both paths use the same SQL guard and reader session, and in both the numbers come from the database, never
from the model.

![Agent analyzer](../png/40-analyzer.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Chat API | Verifies the user's token and queues the turn for a run worker | [One chat turn](32-chat-turn.md) |
| 2 | KPI gate | One model call to Claude Haiku on Amazon Bedrock. It decides whether one standard KPI template fits the question and fills in its values. | [KPI gate](41-kpi-gate.md) |
| 3 | Standard KPI query | A fixed SQL template from the reviewed catalog, run with the values bound as parameters | [Standard KPI path](42-standard-kpi-path.md) |
| 4 | Reply writer | One model call. It writes 1–3 sentences from the formatted rows. | [Standard KPI path](42-standard-kpi-path.md) |
| 5 | Guarded reader | The SQL guard plus a read-only DuckDB session for one customer. Every query of both paths runs here. | [SQL guard and escalation](44-sql-guard-escalation.md) |
| 6 | Custom analysis agent | A Strands agent that explores gold, writes SQL and submits a final query | [Custom analysis agent](43-custom-analysis-agent.md) |
| 7 | Customer B's gold | The `sem.*` views of the current gold version. The lossless tables open only after an audited escalation. | [S3 layout](12-s3-layout.md) |
| 8 | Answer | The same parts on both paths: text, SQL, result table, optional chart, assumptions and caveats | [Custom analysis agent](43-custom-analysis-agent.md) |

## How it works

1. The chat API (1) verifies the token and queues a run. A run worker picks it up, exchanges the token for
   short-lived credentials of the customer's role and opens a read-only reader session.
2. The KPI gate (2) reads the question, the conversation's earlier questions with how each was answered, today's
   date and the data coverage. It returns a structured decision. The server checks it.
3. **Standard KPI path.** If the decision is valid, the template's fixed SQL (3) runs through the guard (5) on gold
   (7). Code formats the numbers, and the reply writer (4) writes a short reply from them.
4. **Custom analysis path.** Anything else goes to the custom analysis agent (6). It runs commands through the same
   guard (5), and may escalate to the lossless tables with a stated reason. It finishes with an answer, a refusal or
   a clarifying question.
5. The answer (8) is built by the server. On the custom path the server runs the agent's final SQL again, so the
   rows in the answer come from the database. The user sees one step that names the path taken:
   "Standard KPI · &lt;template id&gt; · &lt;values&gt;" or "Custom analysis · &lt;gate reason&gt;".

## Specification

**Model and runtime**

| Item | Value |
|---|---|
| Model | Claude Haiku on Amazon Bedrock, effort low (no extended thinking) |
| Agent framework | Strands Agents, `BedrockModel` with Bedrock prompt caching |
| Where it runs | Inside the run worker process (ECS on Fargate). No separate service. |
| Model calls per turn | Standard KPI path: 2 (gate, reply writer). Custom analysis path: 1 + one per agent step. See [Model calls](45-model-calls.md). |
| Typical latency | Standard KPI turn about 4 s. Custom analysis turn about 18 s. |
| Typical cost | Standard KPI turn about $0.004. KPI gate about $0.0004 per call. |

**Data access**

| Item | Value |
|---|---|
| Who reads data | Only the reader session in the run worker, with the customer's temporary role credentials (gate 6: IAM + KMS allow the reader) |
| Default scope | Gold (`sem.*`) of the current version, for the one customer the token names |
| Lossless | Only after the agent's `escalate` command with a stated reason. The reason is masked, audited and shown to the user. |
| Writes | None. Every query is a single read-only SELECT. |
| PHI | Query results are customer data and are treated as PHI. Identifier columns are redacted before any result leaves the tool layer. Up to 20 result rows per query go to Bedrock. |

**Limits** (per question; details on [SQL guard and escalation](44-sql-guard-escalation.md))

| Limit | Value |
|---|---|
| Query time | 25 s per query, 60 s in total per question |
| Rows | 1,000 rows per query; 20 rows shown to the model; 50-row preview in the answer, the rest as a CSV download |
| Agent commands | 12 commands besides the final one (failed commands do not count); hard stop at 30 model turns |
| Output tokens | 4,096 per model call |

**Failure behaviour**

| Event | Result |
|---|---|
| Gate fails in any way (invalid values, exception, timeout, no structured output) | The turn goes to the custom analysis path and the failure is logged. The user never sees a gate error. |
| Standard KPI SQL error or failed reply | Same: the custom analysis path answers |
| Agent ends without a final command | The turn ends with an error part ("I couldn't answer that"), retryable |
| User presses Stop | The in-flight Bedrock stream is cancelled and no further command runs |

**Logging and audit:** every query, escalation and refusal produces an audit record (customer, user, gold
version, ok or failed, row count, seconds). Model usage per turn (input, output, cache read and cache write tokens,
model time) is recorded with the run. See [Audit and logging](53-audit-logging.md).

## Pages in this area

- [KPI gate](41-kpi-gate.md): how the gate decides, what it sees, the server check
- [Standard KPI path](42-standard-kpi-path.md): fixed SQL, display SQL, number formatting, reply writer
- [Custom analysis agent](43-custom-analysis-agent.md): the prompt, the commands, the final answer
- [SQL guard and escalation](44-sql-guard-escalation.md): what every query must pass, and how lossless is opened
- [Model calls](45-model-calls.md): every model call in a turn, caching, latency and cost

## Related

Up: [Overview](00-overview.md) · See also: [One chat turn](32-chat-turn.md) ·
[Customer isolation](23-customer-isolation.md) · [Amazon Bedrock card](90-service-catalog.md)
