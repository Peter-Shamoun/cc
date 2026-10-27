### Amazon Cognito
| | |
|---|---|
| Purpose in ClaimClarity | User pool federated to Microsoft Entra ID (its only identity provider). Maps each user to one customer and issues the id token that names it |
| Talks to | Entra ID (OIDC), the chat API sign-in callback (token endpoint), STS (via the IAM OIDC provider) |
| Permissions | Administrators set `custom:customer` (and `custom:transcript_admin` for customer admins). The app client cannot write these attributes. A pre-authentication check refuses users with no customer. A pre-token-generation step (v2) adds the session tags `customer`, `user` (= `sub`) and, for customer admins, `transcript_admin`, all transitive |
| Encryption | Tokens signed with RS256. All traffic over HTTPS |
| Data held / PHI | User email, Entra object id, customer mapping. No PHI |
| Scaling and limits | Id token 60 minutes. Refresh token 30 days, kept server-side and not given to the browser. Users are created just in time on first sign-in |
| Failure behaviour | No customer mapping: no token. Disabled user: no new sign-ins. Global sign-out revokes refresh tokens; issued id tokens stay valid until they expire |
| Logging and audit | Cognito API calls in CloudTrail. The chat API never logs tokens. See [Sign-in](21-sign-in.md) |
| Used on pages | [Identity & access](20-identity.md) · [Sign-in](21-sign-in.md) · [Token to credentials](22-token-to-credentials.md) · [Network](51-network.md) |
