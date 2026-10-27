# Chat backend

[Overview](00-overview.md) › Chat backend

The chat backend takes a user's question, runs the analyzer on it and streams the answer back to the browser
as it is produced. The chat API only accepts questions and streams answers back. Each question runs on a
separate run worker, fed from a queue. All chat state lives in S3 objects under the customer's prefix, so any
API task can serve any user, and nothing runs on a database.

![Chat backend](../png/30-chat-backend.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| – | Browser | The React chat app. Sends the question and reads the answer as a server-sent event (SSE) stream. | [Edge](31-edge.md) |
| 1 | Edge | CloudFront and AWS WAF serve the web app and pass `/api/*` to the Application Load Balancer | [Edge](31-edge.md) |
| 2 | Chat API | ECS service on Fargate. Checks the token, saves the question, queues a run, streams the run's events. | [Chat turn](32-chat-turn.md) |
| 3 | Run queue | Amazon SQS. One message per question, holding ids only. | [Limits](34-limits.md) |
| 4 | Run workers | ECS service on Fargate. Each worker takes a run, opens the customer's reader session and runs the analyzer. | [Chat turn](32-chat-turn.md) |
| – | Analyzer | The Strands agent inside the worker. The KPI gate sends the question to a standard KPI query or to custom analysis. | [Agent analyzer](40-analyzer.md) |
| 5 | Chat state | Conversations, run events and the agent audit, as S3 objects in the ClaimClarity bucket | [State](33-state.md) |

## How it works

1. The browser loads the web app from CloudFront and calls `/api/*` on the same origin. CloudFront and WAF pass
   API calls to the load balancer.
2. The chat API checks the Cognito id token on every request. It checks the user's and the customer's limits,
   saves the question in the conversation object and puts a run message on the queue. It then opens an SSE
   stream back to the browser.
3. The queue holds the run until a worker is free. Queued runs count toward the customer's limit, so one
   customer cannot fill the queue.
4. A run worker takes the message. It exchanges the user's token at STS for 15-minute credentials of the
   customer's role, opens a read-only session over gold (`sem.*`) and runs the analyzer.
5. The worker appends every event (steps, SQL, table rows, answer text) to numbered run-event chunks in S3. The
   chat API reads new chunks and forwards them to the browser. At the end the worker saves the answer in the
   conversation object.

Run-event chunks let a dropped browser reconnect to any API task and replay what it missed. A cancel flag
lets the user's Stop reach the worker. The [chat turn](32-chat-turn.md) page walks through both.

## Specification

| Item | Value |
|---|---|
| API contract | Stream contract version 1.24.0. `GET /api/contract` returns the version and the JSON schema. |
| Compute | Two ECS services on Fargate in private subnets: the chat API and the run workers. One process per task. |
| Data read | Gold (`sem.*`) by default. Lossless (silver) only when the analyzer states a reason, which is audited. |
| State | S3 objects only, under the customer's prefix and encrypted with the customer's key. No DynamoDB. |
| PHI | Questions, answers, table previews and CSV downloads can hold PHI. They stay inside the customer's prefix and never reach logs or URLs. |
| Auth | `cc_session` cookie (HttpOnly, Secure, SameSite=Strict, Path=/api). Every cookie-authenticated write needs the header `x-cc-csrf: 1`. |
| Main limits | 1 MiB request body, 3 unfinished runs per user, 12 analyzer commands and 60 s of SQL per question. See [Limits](34-limits.md). |

## Pages in this area

- [Edge](31-edge.md): CloudFront, WAF, the web app bucket and the load balancer
- [Chat turn](32-chat-turn.md): one question from POST to the last event, with reconnect and cancel
- [State](33-state.md): conversation objects, run events, the user data key and the agent audit
- [Limits](34-limits.md): rate limits, run caps, budgets and what the user sees

## Related

Up: [Overview](00-overview.md) · See also: [Identity & access](20-identity.md),
[Agent analyzer](40-analyzer.md), [Operations](60-operations.md)
