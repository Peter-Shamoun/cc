### AWS STS
| | |
|---|---|
| Purpose in ClaimClarity | Exchanges a user's Cognito id token for 15-minute credentials of that customer's reader role (AssumeRoleWithWebIdentity, gate 5). Also issues the AssumeRole sessions for the audit writer, the SFTP copy and support break-glass |
| Talks to | IAM (the OIDC provider for the Cognito user pool, role trust policies), the run workers through the STS interface endpoint |
| Permissions | A reader role's trust policy allows `sts:AssumeRoleWithWebIdentity` and `sts:TagSession` only for the ClaimClarity Cognito OIDC provider, only when `aud` is the ClaimClarity app client and `aws:RequestTag/customer` equals that role's customer. The audit writer trusts only the chat API role, with matching `customer` and a `user` tag. Break-glass trusts only the support role, with an external id, a `TICKET-*` reason tag, a customer tag and an operator tag |
| Encryption | HTTPS through the VPC interface endpoint |
| Data held / PHI | None. Session tags carry the customer and the Cognito `sub` |
| Scaling and limits | One call per run, plus one renewal per 12 minutes of a long run. Requested duration 900 s. Role maximum session duration is the AWS minimum, 3600 s. Role chaining is capped at 1 hour by AWS |
| Failure behaviour | `AccessDenied`, `InvalidIdentityToken` or `ExpiredTokenException` end the run with a sign-in-again message. Throttling is retried 3 times with backoff. "Revoke active sessions" on a role denies every session issued before that moment |
| Logging and audit | Every AssumeRole and AssumeRoleWithWebIdentity, allowed or denied, is in CloudTrail with the role and the session name `u-<sub prefix>-<request id>` |
| Used on pages | [Identity & access](20-identity.md) · [Token to credentials](22-token-to-credentials.md) · [Customer isolation](23-customer-isolation.md) · [Network](51-network.md) |
