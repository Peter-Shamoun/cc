# Limits

[Overview](00-overview.md) › [Chat backend](30-chat-backend.md) › Limits

Limits are checked twice: once when a question arrives, and again while its run works. Every limit ends in a clear
message or a normal answer outcome, never a spinner that doesn't stop.

![Limits](../png/34-limits.png)

## In this diagram

| Item | What it does | Details |
|---|---|---|
| XiFin user | Waits out `Retry-After`, then retries up to 2 times | |
| CloudFront + WAF | Rate rule per client address | [Edge](31-edge.md) |
| Chat API | Checks the body size and the run limits | [Chat turn](32-chat-turn.md) |
| S3 · limits object | Per customer: runs in flight, tokens used today, kill switch | [State](33-state.md) |
| Under limits? | Admits the run, or answers 429 | |
| Run queue | Holds admitted runs only | |
| Run worker | Applies the budgets of one question | [Analyzer](40-analyzer.md) |
| Budget left? | Lets the analyzer go on, or ends the run with an answer or an error | |
| Amazon Bedrock | Model calls while budget is left | [Model calls](45-model-calls.md) |

## How it works

1. WAF applies its rate rule per client address.
2. The chat API rejects bodies over 1 MiB and bad requests.
3. It counts the user's unfinished runs and adds a lease to the customer's limits object with an If-Match write.
4. Over a limit, the answer is 429 with `Retry-After`. Otherwise the run is queued.
5. The run worker enforces the per-question budgets below.
6. At the end the worker releases the lease and adds the run's tokens to today's count.

## Specification

| Limit | Value | What the user sees |
|---|---|---|
| WAF rate rule | Per client address, 5-minute window | 429, then retries |
| Request body | 1 MiB; message text 8,000 characters | "Message too long" (413) or 422 |
| Open runs per user | 3 | "Too many answers in progress", `Retry-After: 3` |
| Runs in flight per customer | Setting in the limits object | 429, `Retry-After: 3` |
| Daily tokens per customer | Setting in the limits object | 429 until midnight UTC |
| Kill switch per customer | Setting in the limits object | 503 "Analyst unavailable" |
| Commands per question | 12 | The analyzer answers with what it has, or abstains |
| Model turns per question | 30 | `error`: "The analysis failed. Try again." |
| Output per model call | 4,096 tokens | |
| SQL time | 25 s per statement, 60 s per question | Step error `timeout`, or the analyzer stops querying |
| Rows | 1,000 per query; 50-row preview plus CSV | "Showing n of N rows" |
| Run deadline | 110 s (fits the 120 s ECS stop timeout) | `error` |
| Time in queue | 2 minutes | `error` |
| Clarification | 30 minutes | 409 "Ask again" |

| Item | Value |
|---|---|
| Lease expiry | The run deadline. A crashed worker never holds a slot for longer. |
| Who changes settings | The operations team, through the deploy pipeline only |
| Logging | Each refusal is an audit event with customer, user and reason |

## Related

Up: [Chat backend](30-chat-backend.md) · See also: [Monitoring](62-monitoring.md)
