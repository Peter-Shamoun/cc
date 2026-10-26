# Chat transcripts: two designs

The data-backend design stores EDI and the semantic layer. It does not say where a chat with the analyst agent lives. The owner's draft left that open ("for now we can assume chatlogs are not saved"). This note compares the two options and is the one the simulator implements.

A transcript is PHI, not just operational text. The user types claim numbers, patient names, and member ids. The agent's answer and an escalated result can repeat them. The question is as sensitive as the answer.

What is **not** a transcript:

- **CloudTrail** (S3 data events, KMS decrypts, STS assumes). Keys, principal, and the allow/deny. Not the message and not the rows.
- **Query audit**, kept by both designs below. Who asked, which metric or SQL *hash*, which relations, row count, duration, error code. Not the SQL text (a literal can be a name), not the question, not the result rows.

## Design A — no message persistence

Message text (user, assistant, tool) exists only in the API process that is handling the chat. It is not written to S3, to a database, to logs, or to the query audit. When the process drops it — `close_chat`, crash, deploy, or a second server — the text is gone. A new process cannot resume the thread.

What is still written, per customer, encrypted with that customer's keys:

| Object | Prefix | Contains | Retention |
|---|---|---|---|
| Session metadata | `{customer}/transcripts/users/{sub}/meta/{session}.json` | session id, user sub, customer, timestamps, turn count, model ids, token counts, request ids | 90 days, same lifecycle as Design B, because the prefix is shared. No message text. |
| Query audit | `{customer}/audit/queries/YYYY-MM-DD/{request_id}.json` | the fields listed above | 6 years, Object Lock COMPLIANCE, write-once, not deletable |

The simulator default is this design: `TranscriptStore(..., mode="ephemeral")`, or leave `CC_TRANSCRIPT_MODE` unset.

### What Design A costs

**Audit.** HIPAA's audit-control requirement (45 CFR 164.312(b)) is "record and examine activity in systems that contain or use ePHI." Design A still records that activity: CloudTrail says which objects were read, under whose session, and the query audit says which metric ran and how many rows came back. It does not record the words shown to the user or the words they typed. After the process exits, nobody can reconstruct "what did we tell the biller about this patient." If a chat answer is itself a decision the lab relies on, that gap is the thing compliance has to accept.

**Debugging.** A production incident ("the agent told them the wrong denial rate") cannot be replayed from storage. The query audit can show that `denial_rate` ran against `sem.remit_claims` and returned 12 rows. It cannot show the sentence the model wrapped around those rows, or the follow-up that changed the question. Debugging has to be reproduced on synthetic data or from whatever the process still has in memory.

**Eval.** Real chats cannot be sampled. The bench (authored questions, ledger answers) stays the eval set. There is no production transcript to mine for failures, and no de-identified export, because there is nothing to export. That is a feature until someone tries to grade the agent on live traffic.

**User experience.** Refresh, a new device, or a handoff to a teammate starts a blank chat. There is no history list that opens the old thread. Multi-turn state has to live in the structured plan the agent carries in memory (the SOTA note already prefers the plan over the transcript). The product cannot offer "show me what we discussed last Tuesday."

**Support.** Break-glass has nothing to read. A ticket has the query audit and CloudTrail, not the conversation.

## Design B — persisted transcripts

Turn the store to `mode="persisted"` or set `CC_TRANSCRIPT_MODE=persisted`. Roles and keys exist in either mode; the switch is whether message bodies are written. Terraform sees the same policies either way (`python -m ccsim.infra --export`).

### Layout, key, and the split between PHI and metadata

One prefix and one KMS key per customer, separate from the lake key (`alias/cc/{customer}` stays parser / reader / sftp only):

```
s3://cc-prod/{customer}/transcripts/
  users/{sub}/meta/{session_id}.json          # no message text
  users/{sub}/content/{session_id}/{turn_id}.bin
  users/{sub}/dek.bin                         # wrapped per-user data key
  exports/{export_id}.json                    # redacted; eval role
  admin/salts/{export_id}.bin                 # HMAC salt; admin only
s3://cc-prod/{customer}/audit/queries/YYYY-MM-DD/{request_id}.json
```

The transcript CMK is `alias/cc/{customer}/transcripts`. The query audit uses a different key, `alias/cc/audit`, so disabling the transcript key does not make the audit unreadable.

Message bodies are encrypted twice. The store generates a data key with context `{customer, user, purpose: transcript-dek}`, seals the turn JSON with it, and stores only the wrapped key in `dek.bin`. S3 then SSE-KMS-encrypts those bytes with the transcript CMK. Metadata and the query audit never receive the text. The SQL string passed into `QueryAudit` is SHA-256'd inside the store and dropped; relation names have to match `sem.remit_claims`-style identifiers or the write is rejected.

### Retention and lifecycle

| Data | Retention | Mechanism |
|---|---|---|
| Content, metadata, exports, salts | 90 days | Lifecycle on `{customer}/transcripts/`: current version becomes a delete marker at 90 days, noncurrent bytes are removed the next day. No Object Lock, so a user delete is not blocked. |
| Query audit | 6 years (2190 days) | Same write-once COMPLIANCE shape as raw EDI, on `*/audit/queries/*` only. Deletes are denied. |

90 days is the top of the 30–90 day range already floated. It is a constant (`TRANSCRIPT_RETENTION_DAYS`), not a promise that counsel has signed. Lowering it is a lifecycle change, not a schema change.

### User deletion and crypto-shredding

`delete_my_chats` (the user) and `delete_user_chats` (the customer admin) permanently delete every version under `transcripts/users/{sub}/` and wipe the process's copy of the text and the data key. The query audit is not deleted. HIPAA's Privacy Rule has a right of access and amendment, not a general right to erasure; keeping the content-free audit is the audit-control side of that tension. State law or a customer contract may still require the chat itself to go.

That deletion destroys the account's only copy of the wrapped data key. It does **not** help someone who already copied `dek.bin`. The wrapped key is still decryptable until the customer transcript CMK is disabled.

`crypto_shred_customer` disables that CMK (in prod, the key-admin role: `DisableKey`, no `Decrypt`, no `CreateGrant`, no `PutKeyPolicy`). After that, SSE ciphertext and any copied wrapped data key are unreadable, including backups that stayed under this key. The lake key and the audit key stay enabled, so EDI and the query audit survive a chat shred. A full customer offboard still disables the lake key, as it does today.

Do not re-encrypt transcripts onto a backup key that key-admin cannot disable. That copy would survive the shred.

A per-user CMK would make user deletion itself kill stolen copies, without waiting for a customer-level shred. This design does not do that: the account model is one transcript key per customer, declared like the lake key, not one key per chatting user.

### Who can read what

The DuckDB reader role is not on this list. Agent SQL cannot read or write transcripts. The API server writes the query audit through `cc-api`, which can assume the audit-writer role and cannot read objects.

| Role | Assumes via | Can | Cannot |
|---|---|---|---|
| End user `cc-transcript-user-{c}` | Cognito token, `aws:RequestTag/customer` = c, prefix bound to `aws:PrincipalTag/user` (the sub) | Read, write, delete their own `transcripts/users/{sub}/` | Other users, exports, salts, audit reads, the lake |
| Customer admin `cc-transcript-admin-{c}` | Same, plus session tag `transcript_admin=true` (Entra group `cc-customer-admins`) | Read and delete every user under c; write exports and salts | Other customers, the lake, audit writes |
| Audit reader `cc-audit-reader-{c}` | Admin tag, same trust | Read `audit/queries/` for c | Message content |
| Audit writer `cc-audit-writer-{c}` | `cc-api` only, with session tags `customer` and `user` | Append one audit object, Object Lock, If-None-Match | End users cannot assume it. No Get, no Delete |
| Support `cc-support` | EC2 / SSO | Assume break-glass only | Any S3 read |
| Break-glass `cc-support-breakglass` | Support role, external id, session tags `customer`, `operator`, and `reason` matching `TICKET-*`. Store requests 900 seconds | Read `transcripts/users/` for the tagged customer, all users | Writes, deletes, exports, the lake, any other customer |
| Eval `cc-eval-{c}` | `cc-eval-team` | Read `transcripts/exports/` for that one customer | Content, metadata, salts, audit, the lake |
| Key admin `cc-key-admin` | existing | Disable / schedule deletion of the transcript key | Decrypt |

Break-glass is read-only and ticketed so CloudTrail shows the operator, the ticket, and the customer. AWS will not let a role's max session be under 3600 seconds, so the policy cannot express "15 minutes." The store requests 900 and refuses a longer session it is handed. Alarm on `AssumeRole` of this role with `DurationSeconds` above 900; that is the prod control the policy language cannot make.

Eval exports are redacted (email, SSN, phone, dates, long digit strings, `MRN` / `MBR` / `MEMBER` / `NPI` labels) and the session id is an HMAC whose salt the eval role cannot read. Sessions are not linked back to a user. This is **not** a HIPAA Safe Harbor or expert-determination de-identification: a patient's name in a sentence can remain. Treat the export as still sensitive, inside the BAA, one customer at a time. It is not a training set to ship to a vendor.

### Tenant isolation

Same gates as the lake, not a second mechanism:

- The role's trust checks `aws:RequestTag/customer` from the signed token. A token for A cannot assume B's transcript role, admin role, or audit role.
- The bucket policy denies object access outside `${aws:PrincipalTag/customer}/` and denies a PUT whose KMS key id is not the principal's `kms_key_arn` tag. Transcript roles are tagged with the transcript key; the audit writer is tagged with the audit key. One role cannot write both, and neither is tagged with the lake key.
- The transcript key policy allows decrypt only for this customer's transcript prefix (the end user: only their own sub, via `${aws:PrincipalTag/user}`), and the data-key context has to match the same customer and, for the user, the same sub.
- Break-glass and eval are not exempt from the customer tag. Eval for diagnostics cannot `GetObject` an acme_labs export.

## HIPAA, for both designs

Three rules pull against each other. They are not resolved by picking a storage engine.

**Audit controls versus minimum necessary.** 164.312(b) asks for a mechanism that records activity in systems with ePHI. 164.502(b) says use and disclose only the minimum PHI needed. Design A is the minimum-necessary answer: the activity record has no message content, so a breach of the log bucket is not a breach of the chats. Design B is the audit-control answer if "activity" is taken to include the words disclosed to the user. Keeping both the full transcript and a content-free query audit is coherent: the audit is the long-lived record, the transcript is the short-lived one. Keeping the transcript *instead of* the query audit is not, because a user delete would then erase the activity record.

**Whether the chat is part of the designated record set** (164.524 access, 164.526 amendment) is a legal question, not an engineering one. If a chat answer is the lab's record of why a claim was handled a certain way, Design A may be the wrong default and retention may need to follow the medical record, not 90 days. If the chat is a scratchpad over the semantic layer, and the semantic layer plus the query audit are the record, Design A matches. Counsel has to say which.

**BAAs.** AWS is already the PHI boundary (the prod bucket, KMS, CloudTrail). A transcript prefix in that bucket is covered by the same AWS BAA; it is not a new vendor. The LLM is a separate business associate. The standing decision is Bedrock under a BAA, with invocation logging off or pointed inside the PHI boundary. That decision applies to both designs.

**Provider logs are a copy.** If Bedrock invocation logging, or any tracing vendor, stores prompts and completions, that store is a second copy of the transcript. It has its own retention, its own IAM, and its own breach radius. Design A is void if that copy exists. Design B is undermined if the copy outlives 90 days, sits outside the customer transcript key, or leaves the BAA (a tracing SaaS, a provider console the BAA does not cover, a laptop). Either turn invocation logging off, or deliver it into `{customer}/transcripts/` under the transcript CMK with the same lifecycle and the same roles. Do not add a third copy "for debugging" in CloudWatch with a different retention.

Backups and replicas count too. A snapshot of the process in Design A (a heap dump, a crash report with the request body) is a copy. A snapshot of the bucket in Design B is a copy that only the CMK disable actually destroys.

## Recommendation

Ship **Design A**. Leave the store ephemeral until counsel answers the designated-record-set question and signs a retention number.

The product does not need stored chats to answer a question: the semantic layer and the per-request DuckDB session already do that, and CloudTrail plus the query audit already say who looked at which customer and which metric. Stored chats add a PHI store we would then have to delete, shred, export, and explain. The UX loss (no history across refresh) and the debugging loss (no replay of the sentence) are real and are the reason Design B exists. They are not a reason to turn it on before the retention and the Bedrock logging destination are signed.

When those are signed, turn on Design B with the controls above rather than inventing a third shape. Do not "just log the question." The question is the PHI.

The simulator default matches this: `CC_TRANSCRIPT_MODE` unset means ephemeral. `persisted` is the same code path with content writes enabled. No lake, generator, or parser change is required to flip it.
