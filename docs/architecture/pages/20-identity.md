# Identity & access

[Overview](00-overview.md) › Identity & access

A XiFin user signs in with their company account (Microsoft Entra ID). Amazon Cognito turns that sign-in into an id
token that names exactly one customer. On every question, AWS STS exchanges the token for 15-minute credentials of
that customer's reader role, and those credentials can read only that customer's gold prefix. The customer is never
taken from the request itself, so no request can choose another customer's data.

![Identity & access](../png/20-identity.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | User's browser | Opens the ClaimClarity web app. Holds the session cookie, which page scripts cannot read. | [Sign-in](21-sign-in.md) |
| 2 | Microsoft Entra ID | Company login and MFA. Only members of the groups assigned to the ClaimClarity app get a token. | [Sign-in](21-sign-in.md) |
| 3 | Amazon Cognito | User pool with Entra ID as its only identity provider. Issues the id token that names the customer. | [Sign-in](21-sign-in.md) |
| 4 | Chat API + run workers | Verify the id token on every request (gate 4) and hold it for the run. | [Token to credentials](22-token-to-credentials.md) |
| 5 | AWS STS | AssumeRoleWithWebIdentity: checks the token against the customer role's trust policy (gate 5) and returns 15-minute credentials. | [Token to credentials](22-token-to-credentials.md) |
| 6 | Customer B's gold prefix | The only data the credentials can read: the current version of Customer B's gold tables, decrypted with Customer B's key (gate 6). | [Token to credentials](22-token-to-credentials.md) |

## How it works

1. The user opens the app (1). With no valid session the app sends the browser to sign in.
2. Entra ID (2) signs the user in. A user outside the assigned groups is refused here.
3. Cognito (3) receives the Entra sign-in, finds the customer an administrator mapped to this user, and issues an id
   token (60 minutes) that carries the customer. A user with no customer mapping is refused here.
4. The chat API (4) puts the id token into the `cc_session` cookie: httpOnly, Secure, SameSite=Strict, path `/api`.
   Every API call carries it, and the API verifies it every time (gate 4).
5. For each run, a run worker sends the id token to STS (5). STS checks that Customer B's reader role trusts this
   token (gate 5) and returns credentials that last 15 minutes.
6. With those credentials the worker opens a read-only session on Customer B's gold prefix (6). IAM, the bucket
   policy and Customer B's KMS key must all allow each read (gate 6).

## Specification

| Item | Value |
|---|---|
| Identity provider | Microsoft Entra ID, one enterprise app for ClaimClarity with "Assignment required" on |
| Federation | Cognito user pool, Entra ID as its only OIDC identity provider |
| Customer claim | `custom:customer` in the id token, set by an administrator, not by the user |
| Id token lifetime | 60 minutes. No refresh in the browser: after 60 minutes the user signs in again |
| Session cookie | `cc_session` · httpOnly · Secure · SameSite=Strict · Path=/api · Max-Age 3600 s |
| Token check (gate 4) | Signature (RS256, Cognito signing keys), issuer, audience (app client), expiry, `token_use=id`, customer claim present |
| Credentials | STS AssumeRoleWithWebIdentity into the customer's reader role, 900 s (15 minutes), renewed during long chats |
| Read scope (gate 6) | `GetObject` and a prefix-limited `ListBucket` on the customer's gold prefix only. No older object versions, no raw, no silver, no transcripts |
| Roles per customer | Parser, reader and SFTP copy roles for the lake; separate roles for transcripts and the query audit ([Encryption & keys](52-encryption-keys.md)) |

## Pages in this area

- [Sign-in](21-sign-in.md): Entra ID, Cognito and the session cookie, step by step.
- [Token to credentials](22-token-to-credentials.md): gates 4, 5 and 6, the trust policy and the 15-minute credentials.
- [Customer isolation](23-customer-isolation.md): why Customer A's session cannot reach Customer B's data, layer by layer.

## Related

Up: [Overview](00-overview.md) · See also: [Network & security](50-network-security.md) ·
[Chat backend](30-chat-backend.md) · [IAM and KMS matrix](91-iam-kms-matrix.md)
