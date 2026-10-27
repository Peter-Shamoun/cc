### Amazon SQS (run queue)
| | |
|---|---|
| Purpose in ClaimClarity | Decouples accepting a question from running it. The chat API queues one message per admitted run, and a free run worker takes it. |
| Talks to | Chat API (SendMessage). Run workers (ReceiveMessage with 20 s long polling, DeleteMessage). A dead-letter queue. CloudWatch (metrics). |
| Permissions | The queue policy allows SendMessage only from the chat API task role, and Receive, Delete and ChangeMessageVisibility only from the run worker task role. Access goes through a VPC endpoint, and the policy denies requests from outside it. |
| Encryption | SSE-KMS with a key for the queue. TLS in transit. |
| Data held / PHI | Ids only (message, conversation, customer, user hash) plus the user's Cognito id token, which the worker needs for the STS exchange. Never the question text, which the worker reads from the conversation object. The token is a credential: never logged, and it expires on its own. |
| Scaling and limits | Standard queue. Visibility timeout 150 s (above the 110 s run deadline). Message retention 5 minutes. A worker ends a run as an error without running it if it waited more than 2 minutes. Maximum receive count 1, then the dead-letter queue. Admission per customer happens before SendMessage, so the queue cannot be flooded by one customer. |
| Failure behaviour | A worker that dies leaves its message invisible until the timeout. It then moves to the dead-letter queue and is not run again, because a rerun would repeat model calls and SQL. The user sees the stream stop, the saved conversation reloads, and Retry runs the question again. |
| Logging and audit | `ApproximateNumberOfMessagesVisible` and `ApproximateAgeOfOldestMessage` drive worker scaling and alarms. Any message in the dead-letter queue raises an alarm. Queue configuration changes are recorded in CloudTrail. |
| Used on pages | [Chat backend](../pages/30-chat-backend.md), [Chat turn](../pages/32-chat-turn.md), [Limits](../pages/34-limits.md), [Monitoring](../pages/62-monitoring.md) |
