# ClaimClarity architecture overview

ClaimClarity is a chat assistant for XiFin staff. It answers questions about a clinical lab customer's claims
(837P) and remittances (835), and shows the SQL and data behind every answer. Each customer's data stays inside
that customer's own S3 prefix, KMS keys and IAM roles. A signed-in user reaches only the customer their token
names.

![Overview](../png/00-overview.png)

## In this diagram

| Item | What it does | Details |
|---|---|---|
| **Data in** | | [Ingestion & lake](10-ingestion.md) |
| Customer lab | Sends daily 837, 835 and 277 files | [SFTP intake](11-sftp-intake.md) |
| SFTP intake | AWS Transfer Family receives the files for that customer | [SFTP intake](11-sftp-intake.md) |
| S3 · raw | The files exactly as received, write-once | [S3 layout](12-s3-layout.md) |
| EventBridge | Starts a parser task when a batch is complete | [Parse trigger](13-parse-trigger.md) |
| Parser task | Fargate task, running as the customer's parser role. Builds the lossless and gold tables. | [Parser task](14-parser-task.md) |
| S3 · gold | The curated tables the chat reads | [Gold publish](15-gold-publish.md) |
| **Sign-in** | | [Identity & access](20-identity.md) |
| Microsoft Entra ID | Company sign-in | [Sign-in](21-sign-in.md) |
| Amazon Cognito | Issues the ID token that names one customer | [Sign-in](21-sign-in.md) |
| **A question** | | [Chat backend](30-chat-backend.md) |
| CloudFront + WAF | The only public entry: serves the app and passes API calls on | [Edge](31-edge.md) |
| Chat API | Checks the token and queues one run per question | [One chat turn](32-chat-turn.md) |
| Run queue | SQS queue between the chat API and the run workers | [Limits](34-limits.md) |
| Run worker | Fargate task that runs the analyzer for one run | [Analyzer](40-analyzer.md) |
| STS | Exchanges the token for 15-minute credentials of the customer's reader role | [Token to credentials](22-token-to-credentials.md) |
| Amazon Bedrock | Claude Haiku: every model call | [Model calls](45-model-calls.md) |

Applying to all three flows: [Network & security](50-network-security.md) · [Operations](60-operations.md)

## How it works

1. **Data in.** A lab's files land in its raw prefix. When the batch is complete, a parser task, running as that
   customer's role, writes the lossless tables and publishes a new gold version.
2. **Sign-in.** The user signs in with Entra ID. Cognito issues an ID token naming the customer, which the
   browser keeps in an httpOnly session cookie.
3. **A question.**
   - The chat API checks the token and queues a run.
   - A run worker gets the customer's reader credentials from STS.
   - The analyzer answers from the current gold version: with a preset KPI query when one fits, otherwise with
     its own read-only SQL.
   - The server re-runs the final SQL, so the numbers come from the database.

Appendix: [AWS service catalog](90-service-catalog.md) · [IAM and KMS matrix](91-iam-kms-matrix.md) ·
[Glossary](92-glossary.md) · [Open decisions](93-open-decisions.md)

The examples use a synthetic data set with no real patient data.
