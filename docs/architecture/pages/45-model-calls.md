# Model calls

[Overview](00-overview.md) › [Agent analyzer](40-analyzer.md) › Model calls

This page lists every model call one turn makes, what each call sends, what Bedrock's prompt cache keeps, and what
a turn typically costs in time and money. All calls go to Claude Haiku on Amazon Bedrock through one boto3 session
in the run worker. The parts of each prompt that never change for a customer come first and sit behind a cache
point, so most input tokens of a turn are cheap cache reads.

![Model calls](../png/45-model-calls.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Run worker | Runs one turn of the analyzer (ECS on Fargate) | [One chat turn](32-chat-turn.md) |
| 2 | KPI gate call | One call on every turn. Structured output, no tools. | [KPI gate](41-kpi-gate.md) |
| 3 | Reply writer call | One call on a standard KPI turn. No tools. | [Standard KPI path](42-standard-kpi-path.md) |
| 4 | Agent step calls | One call per step of the custom analysis agent | [Custom analysis agent](43-custom-analysis-agent.md) |
| 5 | Amazon Bedrock | Claude Haiku, with prompt caching | [Amazon Bedrock card](90-service-catalog.md) |

## How it works

1. Every turn starts with the KPI gate call (2).
2. If a template fits, the turn makes one more call, the reply writer (3). A standard KPI turn is two calls.
3. Otherwise the custom analysis agent (4) makes one call per step: each call returns one command, the run worker
   runs it and the result goes into the next call. When the agent issues its final command, the run ends without
   another call. A custom analysis turn is one gate call plus, typically, 4 to 6 agent calls (at most 30).

## Specification

**Every model call in a turn**

| Call | When | Sends | Cached | Output | Typical latency | Typical cost |
|---|---|---|---|---|---|---|
| KPI gate | Every turn, once | System prompt: instructions, 16 templates, allowed values (about 2.9k tokens). Turn input: question, earlier questions and how each was answered, today's date, data coverage. | The system prompt (cache point after it), per customer and gold version | `GateDecision` (structured output) | about 2.3 s | about $0.0004 |
| Reply writer | Standard KPI turns, once | The question, the formatted result rows and the gate's assumptions | No cache point: the input is short and changes every turn | 1–3 sentences, no table | 1–2 s | included in the turn cost below |
| Agent step | Custom analysis turns, once per step | Tool spec and static system prompt (rules, business definitions, generated gold schema, playbook, clarify rules, commands); then the turn message (date, coverage, 3 similar solved examples, question); then the steps so far (commands and results, up to 20 rows each) | Cache point 1 after the tool spec and static prompt, shared by every question of a customer and gold version. Cache point 2 rolls onto the newest message, so each step re-reads what the previous step wrote. | One command line for the command tool | about 18 s for the whole custom turn | see below |

**Per turn**

| Turn | Model calls | Typical latency | Typical cost |
|---|---|---|---|
| Standard KPI path | 2 | about 4 s | about $0.004 |
| Custom analysis path | 1 + one per agent step (typically 5–7 in total, at most 31) | about 18 s | about $0.007 (validation-set average for the custom analysis agent) |

**Settings for every call**

| Setting | Value |
|---|---|
| Model | Claude Haiku on Amazon Bedrock |
| Effort | Low: no extended thinking |
| Max output tokens | 4,096 per call |
| Prompt caching | On (Bedrock prompt caching, Anthropic strategy). A cache entry lives for 5 minutes after its last use, so an active customer's prefixes stay warm. |
| Tools | Gate and reply writer: none. Agent: one command tool, run one call at a time. |
| Cancellation | The user's Stop aborts the in-flight Bedrock stream |
| Access | The run worker's task role may invoke this one model in the ClaimClarity account. No other model is allowed. |

**What is recorded.** Per turn and per model: input tokens, output tokens, cache-read tokens, cache-write tokens,
model time, number of calls and the stop reason. These figures appear in the run's usage record (`message.end.usage`)
and in monitoring. Prompts and completions are not logged by ClaimClarity's servers.

**PHI.** Prompts contain the question, earlier questions of the conversation and query results (up to 20 rows per
query, identifier columns redacted). Treat prompts as PHI. See the [Amazon Bedrock card](90-service-catalog.md)
for how Bedrock handles them.

## Related

Up: [Agent analyzer](40-analyzer.md) · See also: [KPI gate](41-kpi-gate.md) ·
[Custom analysis agent](43-custom-analysis-agent.md) · [Monitoring](62-monitoring.md) · [Limits](34-limits.md)
