### Amazon EventBridge
| | |
|---|---|
| Purpose in ClaimClarity | Starts one parser task when a customer's `_SUCCESS` marker lands in raw |
| Talks to | Amazon S3 (Object Created events from the ClaimClarity bucket); Amazon ECS (RunTask); Amazon SQS (dead-letter queue) |
| Permissions | One rule per customer, matching only `<customer>/raw/received_date=*/_SUCCESS` in the ClaimClarity bucket (gate 1). Its target role, the launcher, may call `ecs:RunTask` on the parser task definitions in the ClaimClarity cluster and `iam:PassRole` on the parser roles to ECS tasks only (gate 2). It has no S3 or KMS access. |
| Encryption | Events carry object keys, not file contents |
| Data held / PHI | None. Object keys hold the customer prefix and received date, never patient data. |
| Scaling and limits | Delivery is at least once, so a duplicate event can start a second task; the parser's batch lease and skip of loaded files make that harmless |
| Failure behaviour | Retries a failed RunTask; after 3 attempts the event goes to the SQS dead-letter queue, from which a day can be redriven |
| Logging and audit | CloudTrail records RunTask, PassRole and failed invocations; the event id is stored with the batch in `edi.ingest_run` |
| Used on pages | [Parse trigger](13-parse-trigger.md) · [Ingestion & lake](10-ingestion.md) · [SFTP intake](11-sftp-intake.md) |
