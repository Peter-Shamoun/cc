### AWS Secrets Manager
| | |
|---|---|
| Purpose in ClaimClarity | Holds the Cognito app client secret that the chat API uses for the code exchange at sign-in. There are no AWS access keys to store: every task uses its task role |
| Talks to | The chat API, through the Secrets Manager interface endpoint |
| Permissions | Only the chat API task role may `GetSecretValue`, on the ClaimClarity secrets only. Only the deploy role writes or rotates them |
| Encryption | A ClaimClarity KMS key |
| Data held / PHI | Secrets only. No PHI |
| Scaling and limits | Read at task start and cached in memory. Rotation is done by the deploy pipeline together with the Cognito app client |
| Failure behaviour | If the secret cannot be read at start, the chat API task fails its health check and does not take traffic |
| Logging and audit | Every `GetSecretValue` is a CloudTrail event. Secret values are never logged |
| Used on pages | [Sign-in](21-sign-in.md) · [Network](51-network.md) |
