# Chat transcripts in the frontend API

Owner: platform team. Design source: `docs/data-backend/chat-transcripts.md` (merged PR #50). This page records the
choice for `api/` and the endpoints the chat UI uses.

## Decision

**Default: Design A (ephemeral).** `CC_TRANSCRIPT_MODE` unset → `api.store.InMemoryTranscriptStore`: message text
lives only in the API process; restart = empty history. This follows the design doc's recommendation until counsel
signs a retention number and the designated-record-set question.

**Opt-in: Design B (persisted)** with `CC_TRANSCRIPT_MODE=persisted` → `api.transcripts.SealedTranscriptStore`, the
same shape as the simulator's design, adapted to the API's message format (parts, not plain turns):

| Object | Contains |
|---|---|
| `{customer}/transcripts/users/{user_hash}/meta/{conversation}.json` | id, customer, createdAt, updatedAt, messageCount. **No text, no title.** |
| `{customer}/transcripts/users/{user_hash}/content/{conversation}.bin` | AES-256-GCM(title + messages) under the user's data key; AAD binds customer, user and conversation |
| `{customer}/transcripts/users/{user_hash}/dek.bin` | the user's data key, wrapped by the customer transcript key (AAD `transcript-dek`) |

* `user_hash` = SHA-256(customer, user) — no email/sub in object names.
* The customer key is `api.transcripts.Keyring`, a local stand-in for the KMS CMK `alias/claimclarity/{customer}/transcripts`.
  `crypto_shred_customer` disables it; afterwards every read for that customer returns **410 `transcripts_unavailable`**.
* "Delete all my chats" removes every object under the user prefix, including `dek.bin`, except `retention.json`
  (the user's setting, not chat data).
* Deleting also clears what lives outside the store. Under one lock with the delete, the API: discards in-flight
  runs for that conversation (they're cancelled, their final transcript write is skipped so the chat can't be
  resurrected, and their resume buffers are dropped at the next gc); removes pending clarifications (they hold the
  question text); and revokes the conversation's CSV downloads (`DownloadRegistry.revoke`). "Delete all" does the
  same for every conversation of that user.
* Retention: 90 days (`TRANSCRIPT_RETENTION_DAYS`), or shorter per user. The user's choice is persisted as
  `{user prefix}retention.json` (`{"days": n}`, no PHI), so it survives restarts. It's enforced on access **and** by
  `store.purge_expired()`, which sweeps every user, including ones who never come back. The app runs the sweep once
  at startup and then every `CC_TRANSCRIPT_SWEEP_SECONDS` (default 3600; 0 = off) from its lifespan. When a sweep
  leaves a user with no conversations, it also deletes their wrapped `dek.bin` (a per-user crypto-shred; the next
  write makes a new key). Audit: `transcripts_expired` (count only).
* `CC_TRANSCRIPT_DIR` stores blobs on disk (0600 files, 0700 dirs, atomic writes, no path traversal);
  `CC_TRANSCRIPT_KEYS_DIR` keeps the demo keys across restarts. Unset = in memory.
* Production swaps `DirBlobs` for the S3 prefix and `Keyring` for KMS; the store code does not change.

Both modes expose the same interface and pass the same test suite (`api/tests/test_transcripts.py`, parametrised).

## Endpoints (all `no-store`, all principal-scoped; another user's id is a 404)

| Method + path | Body | Response |
|---|---|---|
| `GET /api/transcripts` | – | `{mode: "ephemeral"\|"persisted", persisted, retentionDays}` (UI shows "History off · chats live in memory only" when ephemeral) |
| `PUT /api/transcripts/retention` | `{days: 0..3650}` | `{mode, retentionDays}` (persisted mode caps at 90) |
| `GET /api/conversations[?cursor=]` | – | `{items: [{id, title, updatedAt, messageCount}], next?}` newest first, 50 per page |
| `POST /api/conversations/search` | `{q, cursor?}` | same as list, plus `snippet` per hit. **POST because the search text may be PHI and must not be in a URL** (contract §10) |
| `GET /api/conversations/{id}` | – | `{id, title, updatedAt, messages}` |
| `PATCH /api/conversations/{id}` | `{title}` | `{id, title, updatedAt}`; one line, ≤120 chars, empty → "New analysis"; control chars → 422 `invalid_title` |
| `DELETE /api/conversations/{id}` | – | `{deleted: true}` or 404 `{deleted: false}` |
| `DELETE /api/conversations` | – | `{deleted: n}` (delete-my-chats) |

Titles are generic ("New analysis") until the user renames them: the first question can be PHI and is never
echoed as a title. Search runs in-process over the title, user text and assistant text/callout parts (never table
cells). Audit events: `conversation_renamed`, `conversation_deleted`, `conversations_deleted` (count),
`retention_set`; ids and counts only — no titles, no search text.

Web client: `web/src/adapters/conversationsApi.ts` (`ConversationsApi`), keeps nothing in browser storage.

## Not yet
* Edit/regenerate sibling branches are stored as appended messages (`parentId` links); a server-side
  branch view is a contract follow-up.
* Admin read, break-glass and de-identified export from the design doc stay in `ccsim/transcripts.py`.
