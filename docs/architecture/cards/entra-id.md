### Microsoft Entra ID (external)
| | |
|---|---|
| Purpose in ClaimClarity | The company login. One enterprise app for ClaimClarity with "Assignment required" on: only members of the assigned groups can sign in |
| Talks to | Amazon Cognito (OIDC identity provider for the user pool), the user's browser |
| Permissions | IT assigns groups to the app. A separate group marks customer administrators. MFA and Conditional Access follow company policy |
| Encryption | HTTPS. Tokens signed by the tenant's keys and verified by Cognito (issuer, tenant, app client id) |
| Data held / PHI | Company accounts and group membership. No PHI |
| Scaling and limits | Entra id token 60 minutes. The Entra browser session makes re-sign-in after the Cognito token expires a single redirect |
| Failure behaviour | Unassigned user: AADSTS50105. Disabled account: AADSTS50057. Unknown account: AADSTS50034. No ClaimClarity token is issued in any of these cases |
| Logging and audit | Entra sign-in log (successes and refusals), kept per company policy |
| Used on pages | [Identity & access](20-identity.md) · [Sign-in](21-sign-in.md) · [Customer isolation](23-customer-isolation.md) |
