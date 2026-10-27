# ClaimClarity architecture overview

ClaimClarity is a chat assistant for XiFin staff. It answers questions about a clinical lab customer's claims (837P) and
remittances (835) in plain language, and shows the SQL and data behind every answer.

Each customer's data stays inside its own boundary: its own S3 prefix, its own KMS key and its own IAM role. A
signed-in user can only reach the one customer their token names. The design is built on three principles:
- The AI interprets the question.
- Governed data and fixed SQL compute the numbers.
- Every data access is checked by AWS and recorded.

![Overview](../png/00-overview.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Customer labs | Send daily 837, 835 and 277 files | [SFTP intake](11-sftp-intake.md) |
| 2 | SFTP intake | AWS Transfer Family lands each file in the customer's raw prefix | [SFTP intake](11-sftp-intake.md) |
| 3 | Parser task | One Fargate task per batch, running as the customer's role. It writes the lossless (silver) tables and publishes a new gold version. | [Ingestion & lake](10-ingestion.md) |
| 4 | ClaimClarity bucket | One S3 bucket, with a prefix, KMS key and IAM role per customer. It holds raw, silver, gold and all chat state. | [S3 layout](12-s3-layout.md) |
| 5 | XiFin users | Use the chat app in the browser | [Chat backend](30-chat-backend.md) |
| 6 | CloudFront + WAF | Serve the web app and pass API calls to the chat API | [Edge](31-edge.md) |
| 7 | Chat API | Verifies the token, queues one run per question, streams the answer back | [One chat turn](32-chat-turn.md) |
| 8 | Analyzer | Strands agent in the run workers. A KPI gate sends each question to a standard KPI query or to custom analysis. | [Agent analyzer](40-analyzer.md) |
| 9 | Amazon Bedrock | Claude Haiku: the only model the analyzer calls | [Model calls](45-model-calls.md) |
| 10 | Amazon Cognito | Federates Entra and issues the id token that names the customer | [Identity & access](20-identity.md) |
| 11 | Microsoft Entra ID | Company sign-in. The ClaimClarity app is assigned to groups. | [Sign-in](21-sign-in.md) |
| 12 | Network & security | Private subnets, VPC endpoints, KMS per customer, CloudTrail and the agent audit | [Network & security](50-network-security.md) |
| 13 | Operations | Images, deploys, monitoring and the regression set that gates analyzer changes | [Operations](60-operations.md) |

## How it works

**Data in**
1. A customer lab sends its daily files over SFTP (1, 2). They land in that customer's raw prefix (4).
2. When a batch is complete, EventBridge starts a parser task as that customer's role (3). It writes the
   lossless tables, then publishes a new version of the gold tables.

**Sign-in**
1. A XiFin user signs in through Cognito (10), which federates to Entra ID (11).
2. Cognito issues an id token that names the customer. The browser holds it in an httpOnly session cookie.

**Questions**
1. The user asks a question (5). It goes through CloudFront and WAF (6) to the chat API (7), which verifies the
   token and queues a run.
2. A run worker exchanges the token at STS for 15-minute credentials of that customer's role. It opens a
   read-only session on the current gold version only.
3. The analyzer (8) asks the KPI gate whether a standard KPI query answers the question exactly:
   - **Yes:** fixed SQL runs with the values filled in.
   - **No:** the custom analysis agent writes SQL over gold behind a guard.

   Every model call goes to Claude Haiku on Bedrock (9).
4. The server runs the final SQL itself, so the numbers come from the database. The answer streams back with its
   SQL and result. The conversation, run events and an audit record of every query are stored under the
   customer's prefix, encrypted with the customer's key (4).

## Areas

- [Ingestion & lake](10-ingestion.md): SFTP intake, S3 layout, the parse trigger, the parser task, gold publishing
- [Identity & access](20-identity.md): sign-in, token to credentials, customer isolation
- [Chat backend](30-chat-backend.md): edge, one chat turn, state, limits
- [Agent analyzer](40-analyzer.md): KPI gate, standard KPI path, custom analysis agent, SQL guard, model calls
- [Network & security](50-network-security.md): network, encryption and keys, audit and logging
- [Operations](60-operations.md): deploy, monitoring, evaluation and regression

Appendix: [AWS service catalog](90-service-catalog.md) · [IAM and KMS matrix](91-iam-kms-matrix.md) ·
[Glossary](92-glossary.md)

The diagrams and examples use a synthetic data set of two customers (acme_labs, acme_diagnostics). It contains
no real patient data.
