# Chat turn

[Overview](00-overview.md) › [Chat backend](30-chat-backend.md) › Chat turn

One chat turn is one question and its streamed answer. The chat API never runs the analyzer itself. It saves
the question, queues a run and relays the events that the run worker writes to S3. Because the events are
stored, a dropped connection can resume on any API task, and Stop reaches the worker through a flag object.

![Chat turn](../png/32-chat-turn.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| – | Browser | Sends the question, reads the SSE stream, reconnects with `Last-Event-ID`, sends Stop | |
| 1 | Chat API | Checks the token and limits, saves the question, queues the run, relays events | [Limits](34-limits.md) |
| 2 | Run queue | Amazon SQS. Holds the run until a worker takes it. | [SQS card](../cards/sqs.md) |
| 3 | Run worker | Opens the reader session, runs the analyzer, writes events and the answer | [ECS card](../cards/ecs-fargate.md) |
| – | Analyzer | KPI gate, then the standard KPI query or custom analysis | [Agent analyzer](40-analyzer.md) |
| 4 | Chat state | The conversation object, run-event chunks and the cancel flag in the customer's prefix | [State](33-state.md) |

## How it works

**Ask**

1. The browser sends `POST /api/chat` with the session cookie and `x-cc-csrf: 1`. The body has the
   `conversationId` (or none for a new chat) and the user message (at most 8,000 characters). The chat API
   checks the token ([gate 4](22-token-to-credentials.md)), the body size and the request shape. A request
   that names a customer, database, schema or tables is refused with 422: the data scope comes only from the
   token. The chat API then admits the run against the user's and the customer's limits
   ([Limits](34-limits.md)).
2. The chat API appends the user message to the conversation object with an If-Match write
   ([State](33-state.md)).
3. It sends a run message to the queue. The message holds ids only (message, conversation, customer and user
   hash) plus the user's id token for the STS exchange. It never holds the question text.
4. It answers with `200 text/event-stream` and keeps the connection open.
5. A run worker receives the message. It exchanges the token at STS for 15-minute credentials of the
   customer's role ([gates 5 and 6](22-token-to-credentials.md)) and reads the question from the conversation
   object.
6. The worker asks the analyzer for an answer.
7. The analyzer reports every command as it starts and ends. The worker turns these into contract events:
   `message.start`, `status`, `step.explore`, `step.sql_running`, `step.sql_result`, `step.repair_attempt`,
   `step.check`, `text.*`, `table.start`, `table.rows`, `chart.*`, `part` and finally `message.end`.
8. The worker numbers each event (`seq` starts at 0, gapless) and appends it to a run-event chunk. A chunk is
   written every 64 events, every 1 s, and at `message.end`.
9. The chat API reads new chunks every 0.5 s.
10. It forwards each event to the browser as one SSE frame (`id: <messageId>:<seq>`, `data: <event JSON>`).
    After 15 s with nothing to send it sends a `heartbeat` (no `id`).
11. At the end the worker saves the assistant message (parts, steps, usage, sources) in the conversation object.
    It never creates the conversation, so an answer cannot bring back a chat that was deleted meanwhile. It
    deletes the queue message.

**Reconnect**

12. If the connection drops before `message.end`, the browser calls `GET /api/chat/{messageId}/stream` with
    `Last-Event-ID: <messageId>:<seq>`. It retries after 250 ms, 1 s, 2 s and 4 s. Any API task can serve it.
13. The chat API replays every stored event with a larger `seq`, then follows new chunks live. The browser
    drops any event with `seq` at or below the last one it has, so overlaps are harmless. Replay is available
    until 60 s after the run ends. After that the answer is `404 stream_gone` and the browser reloads the
    conversation with `GET /api/conversations/{id}`.

**Cancel**

14. The user presses Stop. The browser sends `POST /api/chat/{messageId}/stop`, and the answer is
    `{"stopped": true}`. Stop is idempotent. Another user's or an unknown run gets 404.
15. The chat API writes a cancel flag object next to the run's event chunks.
16. The worker checks for the flag every 0.5 s. When it is set, the worker interrupts the running SQL statement
    (and checks again every 50 ms until the run ends), closes everything still open and writes
    `message.end {finishReason: "aborted"}`. Each open `step.sql_running` gets `step.sql_result {ok: false,
    error.code: "cancelled"}`, and each open text or reasoning part gets its end event.
17. The chat API relays the final `message.end` to the browser, within about 1 s of Stop. An aborted answer is
    saved without parts or steps.

A dropped browser is not a cancel. If no client is attached, the run keeps going for 10 s. If nobody
reconnects in that time, the chat API writes the cancel flag as if Stop had been pressed.

## Specification

### Endpoints of one turn

| Call | Success | Errors the client acts on |
|---|---|---|
| `POST /api/chat` | 200 `text/event-stream` | 401 `auth_expired`, 403 missing CSRF header, 404 another user's conversation, 409 `clarification_expired`, 413 `payload_too_large`, 422 `validation`, 429 `rate_limited` with `Retry-After` |
| `GET /api/chat/{messageId}/stream` | 200 `text/event-stream`, replay then live | 404 `stream_gone` |
| `POST /api/chat/{messageId}/stop` | 200 `{"stopped": true}` | 404 `{"stopped": false, "error": {"code": "not_found"}}` |
| `GET /api/download/{csvFileId}` | 200 `text/csv` attachment | 404 when unknown, expired or another user's |

Every error body is `{"error": {"code", "message"}}`. Every response carries `Cache-Control: no-store` and the
API security headers ([Edge](31-edge.md)).

### Outcomes (`message.end.finishReason`)

| Value | Meaning |
|---|---|
| `stop` | Answered: answer text, the SQL (literals that could be PHI are shown as `?`), a table preview of at most 50 rows, a CSV download, optional chart |
| `abstain` | The analyzer declines on purpose (for example a PHI request) and suggests up to 6 rephrasings |
| `needs_input` | A clarification form (1 to 4 questions). The reply is a new `POST /api/chat` with `resume`, valid for 30 minutes. |
| `error` | One error part ("The analysis failed. Try again.") with no internal detail |
| `aborted` | Stopped by the user or by the disconnect grace period |

### Timings

| Item | Value |
|---|---|
| Heartbeat | 15 s of silence |
| Run-event chunk | 64 events, 1 s, or `message.end`, whichever comes first |
| API read of new chunks | every 0.5 s |
| Disconnect grace | 10 s |
| Stop to `message.end` | about 1 s |
| Replay window | 60 s after `message.end` |
| Stored-replay end | the chat API ends a replay if no new event arrives for 60 s (a worker died), and the client reloads |
| Client reconnect | 250 ms, 1 s, 2 s, 4 s, then reload. The client also reconnects after 45 s of silence. |

### PHI rules on the wire

- Step events carry counts, column names and types, never row values. Row values appear only in `table.rows`,
  chart points and the CSV download.
- Shown SQL has PHI-bearing literals replaced by `?` (`maskedLiterals` counts them). The SQL that runs is
  unchanged.
- Error messages in events are scrubbed (quoted literals, dates, long digit runs masked) and cut to 500 characters.
- No PHI in URLs. Paths carry opaque ids only. Search is a POST, because its text can be PHI.
- The browser never writes rows, SQL, steps or message text to local storage.

### Failure behaviour

- An S3 chunk write that fails is logged and the run goes on. The live stream from the same worker is not
  affected. Only a later replay can miss events.
- A worker that dies mid-run is not retried, because a second run would repeat the SQL and the model calls.
  Its stream stops. After 60 s with no new events the chat API ends the stream. The client reloads the
  conversation, and the user can press Retry.
- A conversation deleted mid-run: the delete marks the run discarded and sets the cancel flag. No chunk and no
  answer is written after that.

## Related

Up: [Chat backend](30-chat-backend.md) · See also: [State](33-state.md), [Limits](34-limits.md),
[Token to credentials](22-token-to-credentials.md), [Agent analyzer](40-analyzer.md)
