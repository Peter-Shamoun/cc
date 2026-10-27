# Sign-in

[Overview](00-overview.md) › [Identity & access](20-identity.md) › Sign-in

Sign-in has two independent checks. Entra ID decides whether this person may use ClaimClarity at all, and Cognito
decides which one customer they work for. The result is a Cognito id token that the chat API keeps in an httpOnly
cookie, so no script in the page can read or copy it.

![Sign-in](../png/21-sign-in.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Browser | Opens the app. With no valid `cc_session` cookie, every API call answers 401 `auth_expired` and the app goes to sign-in. | [Edge](31-edge.md) |
| 2 | Microsoft Entra ID | Company login, MFA and Conditional Access. Issues a token only to members of the assigned groups. | [Entra ID card](90-service-catalog.md) |
| 3 | Amazon Cognito | Verifies the Entra token, finds the user's customer and issues the id token. | [Cognito card](90-service-catalog.md) |
| 4 | Chat API sign-in | The OAuth callback. Exchanges the authorization code for tokens and verifies the id token. | [Token to credentials](22-token-to-credentials.md) |
| 5 | Session cookie | `cc_session` holds the id token. httpOnly, Secure, SameSite=Strict, Path=/api, 60 minutes. | |
| 6 | Not assigned | Entra refuses a user outside the assigned groups (error AADSTS50105). Disabled or unknown accounts are refused too. | |
| 7 | No customer | Cognito refuses a user that no administrator has mapped to a customer. No token is issued. | |

## How it works

1. The browser (1) has no valid session. The app sends it to the Cognito authorize endpoint, with Entra ID named as
   the identity provider.
2. Cognito redirects the browser to Entra ID (2). The user signs in with their company account and MFA. The
   ClaimClarity enterprise app has "Assignment required" on, so Entra issues a token only if the user is in one of
   the assigned groups. Otherwise the sign-in stops (6).
3. Entra returns to Cognito (3). Cognito verifies the Entra id token: signature, issuer, the ClaimClarity tenant and
   the app's client id. On first sign-in it creates the Cognito user just in time, keyed by the Entra object id.
4. Cognito's pre-authentication check refuses a user with no `custom:customer` attribute (7). An administrator sets
   that attribute. Users cannot write it.
5. Cognito's pre-token-generation step adds the customer and the user's `sub` to the token as STS session tags.
   Customer administrators also get a `transcript_admin` tag.
6. Cognito redirects the browser to the chat API's sign-in callback (4) with an authorization code. The API exchanges
   the code at the Cognito token endpoint, using the app client secret from Secrets Manager.
7. The API verifies the id token (the same check as gate 4), sets the `cc_session` cookie (5) and redirects to the
   path the user started from (a site path only, never another host).
8. From then on, every call to `/api/*` carries the cookie. Calls that change data also send the header
   `x-cc-csrf: 1`. After 60 minutes the API answers 401 and the app sends the user through sign-in again. While
   the Entra session is still open, that takes one redirect and no typing.

## Specification

### The Cognito id token

| Claim | Value |
|---|---|
| `iss` | The Cognito user pool's issuer URL |
| `aud` | The ClaimClarity app client id |
| `token_use` | `id` |
| `sub` | The Cognito user id. Conversations are keyed by customer and `sub`, never by email |
| `email` | The user's Entra sign-in name |
| `custom:customer` | The one customer this user works for. Names the role STS will assume |
| `https://aws.amazon.com/tags` | Session tags for STS: `customer`, `user` (= `sub`), and `transcript_admin` for customer administrators. All transitive |
| `iat`, `auth_time`, `exp` | Issued now, expires 3600 s later |
| Signature | RS256 with the user pool's signing key |

### The session cookie

| Attribute | Value | Why |
|---|---|---|
| Name | `cc_session` | |
| Value | The Cognito id token | Verified on every request |
| HttpOnly | yes | Page scripts cannot read it, so an injected script cannot steal it |
| Secure | yes | Sent over HTTPS only |
| SameSite | Strict | Not sent on cross-site requests |
| Path | `/api` | Sent to the API only, not with static files |
| Max-Age | 3600 s | Same as the id token |

The API reads the token only from the cookie or an `Authorization` header, never from a URL. Cookie-carried writes
without `x-cc-csrf: 1` are refused with 403. The API grants no CORS, so another site cannot add that header.

### Lifetimes and revocation

| Item | Lifetime | Revocation |
|---|---|---|
| Entra id token | 60 minutes | Disable the Entra account or remove it from the assigned groups |
| Cognito id token | 60 minutes | An id token stays valid until it expires. Disabling the Cognito user stops new sign-ins at once |
| Cognito refresh token | 30 days, kept by Cognito | Not given to the browser. Global sign-out revokes it |
| STS credentials | 15 minutes | "Revoke active sessions" on the customer's role denies every session issued before that moment |

To lock a user out at once: disable the Cognito user, run global sign-out, and revoke active sessions on that
customer's reader role. Already-issued id tokens then cannot get new credentials from STS.

### Sign-out

`POST /api/auth/sign-out` revokes the user's Cognito tokens, sends `Clear-Site-Data: "cache", "storage"` and expires
the cookie. It needs the CSRF header like every write. Other open tabs leave for sign-in too.

### Failure behaviour

| Case | Result |
|---|---|
| User not assigned to the app | Entra refuses (AADSTS50105). No token |
| User disabled in Entra | Entra refuses (AADSTS50057) |
| No customer mapping | Cognito refuses at pre-authentication. No token |
| Code exchange fails | Sign-in page shows a generic error. Nothing is logged except the error type |
| Expired cookie | 401 `auth_expired`. The app goes to sign-in once, with the current path as `next` |

### Logging

Entra keeps its sign-in log (successes and refusals). Cognito sign-in events go to CloudTrail. The API records a
`signed_out` audit event with customer and user only. The token itself is never logged. See
[Audit & logging](53-audit-logging.md).

## Related

Up: [Identity & access](20-identity.md) · Next: [Token to credentials](22-token-to-credentials.md) · See also:
[Edge](31-edge.md) · [Network](51-network.md) (how the API reaches Cognito)
