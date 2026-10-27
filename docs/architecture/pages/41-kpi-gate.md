# KPI gate

[Overview](00-overview.md) › [Analyzer](40-analyzer.md) › KPI gate

The KPI gate is the first step of every question: one call to Claude Haiku that decides whether one preset KPI
query answers the question exactly and fills in its values. The server then checks the values, and anything that
does not fit or fails goes to the Claim Query Agent.

![KPI gate](../png/41-kpi-gate.png)

## In this diagram

| Item | What it does (one sentence) | Details |
|---|---|---|
| Chat app | Sends the user's question. | [Chat turn](32-chat-turn.md) |
| Cached: 16 presets · allowed values | The gate's instructions, the preset catalog and the allowed values, cached for each customer and gold version. | [Model calls](45-model-calls.md) |
| Each turn: history · date · data coverage | Earlier questions and how each was answered, today's date and the dates the data covers. | |
| KPI gate | Returns a decision: does a preset fit, which one, with which values and assumptions. | |
| Claude Haiku on Amazon Bedrock | Makes the decision; the gate has no tools. | [Model calls](45-model-calls.md) |
| Values allowed? | Server code that accepts the decision only if the preset exists and every value is allowed. | |
| Preset KPI queries | Run when both checks say yes. | [Preset KPI queries](42-standard-kpi-path.md) |
| Claim Query Agent | Answers when either check says no. | [Claim Query Agent](43-custom-analysis-agent.md) |

## How it works

1. The question arrives with the conversation history, today's date and the data coverage. History lets "and for
   Cigna?" keep the measure of the question before.
2. The gate asks Claude Haiku whether one preset computes exactly what was asked. Any extra filter, other grouping,
   comparison, "why", several numbers or different definition means no. When unsure, no. The gate never asks the
   user anything.
3. The server checks the decision: the preset exists, the values fill exactly its parameters, and every value is
   in its allowed list.
4. Yes: the preset runs. No, or any failure: the Claim Query Agent answers.
5. The user sees one step: "Standard KPI · &lt;preset&gt; · &lt;values&gt;" or "Custom analysis · &lt;reason&gt;".

## Specification

| Item | Value |
|---|---|
| Decision | `fits`, `template_id`, `values`, `assumptions`, `reason` (structured output) |
| Allowed values | Computed once per customer and gold version, through the SQL guard: payer names, payer classes, CARC codes, CPT codes, payment months, first-submission months |
| Preset catalog | `analyzer/kpi_templates.yaml`, version 1, 16 presets. Changes are reviewed. |
| Model | Claude Haiku on Amazon Bedrock, no tools, same session as the agent |
| Data in the prompt | Payer names and codes; the question and conversation history. No query rows. |
| Failure | Invalid values, an exception, a timeout, no structured output: the question goes to the Claim Query Agent and the failure is logged. The user never sees a gate error. |
| Logging | The run records the decision (preset id and values, or "custom analysis") and any failure type |

## Related

Up: [Analyzer](40-analyzer.md) · See also: [Preset KPI queries](42-standard-kpi-path.md) · [Model calls](45-model-calls.md)
