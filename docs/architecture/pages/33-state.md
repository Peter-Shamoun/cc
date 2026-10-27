# State

[Overview](00-overview.md) › [Chat backend](30-chat-backend.md) › State

All chat state is kept in S3 objects under the customer's prefix in the ClaimClarity bucket, with no database. A
conversation is one object, changed with an If-Match write, and its text is sealed with a per-user data key.

![State](../png/33-state.png)

## In this diagram

| Item | What it does | Details |
|---|---|---|
| Chat API | Reads and changes conversations | [Chat turn](32-chat-turn.md) |
| Conversation | One sealed object per chat: title, messages, pinned and archived, feedback | |
| User data key | A per-user AES-256 key. It seals the conversations and event chunks, and is stored only in wrapped form. | |
| Customer B's transcript key | KMS key that wraps every user data key | [Encryption & keys](52-encryption-keys.md) |
| Run worker | Writes event chunks, the answer and the audit | [Analyzer](40-analyzer.md) |
| Run-event chunks | Numbered write-once objects. Any API task can replay from them. | [Chat turn](32-chat-turn.md) |
| Agent audit record | One record per SQL statement, written once per request | [Audit & logging](53-audit-logging.md) |
| Query audit key | The platform key for audit records, with one statement per customer | [Encryption & keys](52-encryption-keys.md) |

## How it works

1. To change a conversation, the chat API reads the object and its ETag, changes it, and writes it back with
   `If-Match`. A new conversation is written with `If-None-Match: *`.
2. If another task wrote first, S3 answers 412. The API reads again and retries, up to 8 times with jittered backoff.
3. The run worker writes event chunks once each (`If-None-Match: *`). It appends the answer with the same If-Match loop.
4. Every seal binds customer, user and object name, so a copied blob does not open elsewhere.
5. At the end of the run, the run worker writes the run's agent audit records (one per SQL statement) as one
   write-once object, through Customer B's audit writer role.

## Specification

### Objects (under `B/transcripts/users/{user}/` unless noted)

| Object | Key | PHI |
|---|---|---|
| Conversation index | `meta/{conversation}.json`: ids, times, counts | No |
| Conversation | `content/{conversation}.bin` | Yes |
| Run-event chunk | `runs/{conversation}/{message}/{000000}.bin` | Yes |
| Cancel flag | `runs/{conversation}/{message}/cancel` | No |
| User data key | `dek.bin`, wrapped | No |
| CSV download | `downloads/{fileId}.bin`, 15 minutes | Yes |
| Limits object | `B/transcripts/limits.json`: runs in flight, tokens used today, kill switch; If-Match writes | No |
| Conversation owner | `B/transcripts/conversation-owners/{conversation}.json` | No |
| Agent audit | `B/audit/queries/{date}/{request}.json`: one object per request; ids, outcome, row count and the first 500 characters of the SQL | Yes |

`{user}` is a 32-character hash of the customer and user ids.

### Access, keys and retention

| Item | Value |
|---|---|
| Chat API and run worker | Customer B's transcript role, session-tagged with the user: only that user's prefix, plus Get and Put on the limits object |
| Audit | Customer B's audit writer role can only put records. The audit reader role can only read them. |
| Keys | Bucket SSE-KMS with Customer B's transcript key. Application seal: AES-256-GCM with the user data key. Audit records use the query audit key. |
| Retention | Conversations 90 days after the last change (users can choose less). Swept hourly. Agent audit 6 years, Object Lock, never deleted. |
| Delete | Deleting all chats removes the user's prefix, data key included. Disabling Customer B's transcript key makes every chat unreadable (410). |
| Temporary chats | Never saved. Held in one chat API task's memory for 1 hour idle, at most 20 per user. Their event chunks are deleted 60 s after the run. |
| Failure | Still conflicting after 8 tries: the request fails ("This conversation changed"). Nothing here logs text. |

## Related

Up: [Chat backend](30-chat-backend.md) · See also: [S3 layout](12-s3-layout.md), [Customer isolation](23-customer-isolation.md)
