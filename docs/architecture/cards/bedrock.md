### Amazon Bedrock

| | |
|---|---|
| Purpose in ClaimClarity | The only model service. Runs Claude Haiku for the analyzer: the KPI gate, the reply writer on the standard KPI path, and every step of the custom analysis agent. |
| Talks to | Called only by the run workers (ECS on Fargate), through the Strands `BedrockModel` (Converse streaming API). Bedrock calls nothing in ClaimClarity and has no access to the ClaimClarity bucket. |
| Permissions | The run worker's task role may invoke the Claude Haiku model (its inference profile and the foundation model behind it), streaming and non-streaming. No other role, model or Bedrock feature (agents, knowledge bases, fine-tuning) is allowed. The chat API and the parser cannot call Bedrock. |
| Encryption | TLS in transit. Bedrock does not store prompts or completions; model invocation logging is off. |
| Data held / PHI | Prompts contain the question, earlier questions of the conversation and query results (up to 20 rows per query, identifier columns redacted), so they are PHI. Nothing is stored after the call except the prompt cache, which keeps the shared prompt prefixes in memory for 5 minutes after their last use. Inputs and outputs are not used to train models and are not shared with the model provider. Bedrock is a HIPAA-eligible service under the AWS BAA. |
| Scaling and limits | On-demand, within the account's Bedrock quotas (requests and tokens per minute for the model). Per call: 4,096 output tokens, effort low (no extended thinking). Per turn: 2 calls on the standard KPI path; 1 + one per step on the custom analysis path, at most 30 agent steps. |
| Failure behaviour | A failed or timed-out KPI gate call sends the turn to the custom analysis path. A failed reply-writer call does the same. A failed agent call ends the turn with a retryable error. The user's Stop aborts the in-flight stream. |
| Logging and audit | CloudTrail records the Bedrock API calls (caller role, model, time), without prompt content. Each run records token usage per model (input, output, cache read, cache write), model time and stop reason. |
| Used on pages | [Agent analyzer](40-analyzer.md) · [KPI gate](41-kpi-gate.md) · [Standard KPI path](42-standard-kpi-path.md) · [Custom analysis agent](43-custom-analysis-agent.md) · [Model calls](45-model-calls.md) |
