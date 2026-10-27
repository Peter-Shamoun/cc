# Token to credentials

[Overview](00-overview.md) › [Identity & access](20-identity.md) › Token to credentials

Every question goes through three AWS checks before a single byte of customer data is read. The chat API verifies the
id token (gate 4). STS checks the customer role's trust policy (gate 5). IAM and KMS check every read (gate 6). The
worker holds no standing AWS access to customer data: it gets 15-minute credentials for one customer's reader role,
for one run.

![Token to credentials](../png/22-token-to-credentials.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Browser | Sends the `cc_session` cookie with each API call | [Sign-in](21-sign-in.md) |
| 2 | Chat API | Verifies the id token (gate 4). Takes the customer from the token only, then queues the run | [Chat turn](32-chat-turn.md) |
| 3 | Run worker | Holds the verified id token for this run and calls STS | [Chat backend](30-chat-backend.md) |
| 4 | AWS STS | AssumeRoleWithWebIdentity into Customer B's reader role (gate 5) | [STS card](90-service-catalog.md) |
| 5 | Reader session | A DuckDB session opened with the 15-minute credentials, limited to B's gold relations | [SQL guard & escalation](44-sql-guard-escalation.md) |
| 6 | B's gold prefix | `B/semantic_layer/` in the ClaimClarity bucket. The session reads the version that `CURRENT` names | [Gold publish](15-gold-publish.md) |
| 7 | Cognito signing keys | The user pool's public keys (JWKS). The API caches them | [Network](51-network.md) |
| 8 | Trust policy | Customer B's reader role trusts only Cognito tokens for the ClaimClarity app that carry `customer = B` | |
| 9 | Customer B's key | KMS key that decrypts B's objects, only for B's roles, only through S3 | [Encryption & keys](52-encryption-keys.md) |

## How it works

1. The browser (1) calls the API with the `cc_session` cookie.
2. **Gate 4 · Is the token valid?** The chat API (2) verifies the id token against the Cognito signing keys (7):
   - the algorithm is RS256 and the key id is one of the pool's keys (no `alg=none`, no HS256);
   - the signature is valid;
   - `iss` is the user pool and `aud` is the ClaimClarity app client;
   - `exp` has not passed and `token_use` is `id`;
   - `custom:customer` is present.
   Any failure answers 401 `auth_expired`. The API then uses `custom:customer` and `sub` from the token as the
   customer and user. Nothing in the request body, query string or other headers can change them.
3. The API queues the run. A run worker (3) picks it up with the verified token.
4. **Gate 5 · Does B's role trust this token?** The worker calls STS (4) with AssumeRoleWithWebIdentity:
   - role: Customer B's reader role, chosen from the token's customer claim;
   - session name: `u-<first 8 characters of sub>-<request id>`, so CloudTrail shows who and which request;
   - duration: 900 seconds.
   STS verifies the token again, against the OIDC provider registered for the Cognito user pool, and evaluates the
   role's trust policy (8). The token's session tags become the session's `customer` and `user` tags.
5. STS returns credentials that expire after 15 minutes. The worker opens the reader session (5):
   - it reads `B/semantic_layer/CURRENT`, then that version's manifest;
   - it creates one view per `sem.*` table over exactly the files the manifest lists;
   - each relation's prefix gets its own scoped DuckDB secret and allowed directory;
   - external file access is off, there is no temp directory, memory is capped at 2 GB, and the configuration is
     locked.
6. **Gate 6 · Do IAM and KMS allow this read?** Each S3 read (6) must pass B's reader role policy, the bucket policy
   and B's key policy (9). The key decrypts only through S3, only for objects under `B/`.
7. Long chats renew the credentials. Before each SQL call the worker checks the credentials' expiry. With fewer than
   3 minutes left, it calls AssumeRoleWithWebIdentity again with the same id token and reopens the reader session
   on the same gold version. A SQL call is never run on expired credentials: it is refused and audited as
   `expired`. Once the id token itself expires (60 minutes after sign-in), STS refuses the renewal, the run ends,
   and the user signs in again.

## Specification

### Trust policy of Customer B's reader role (gate 5)

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Principal": { "Federated": "<OIDC provider for the ClaimClarity Cognito user pool>" },
    "Action": ["sts:AssumeRoleWithWebIdentity", "sts:TagSession"],
    "Condition": {
      "StringEquals": {
        "<user pool issuer>:aud": "<ClaimClarity app client id>",
        "aws:RequestTag/customer": "B"
      }
    }
  }]
}
```

| Condition | Effect |
|---|---|
| Principal is the Cognito OIDC provider | Only tokens from the ClaimClarity user pool. An Entra token or another pool's token is refused |
| `aud` = the ClaimClarity app client | A token issued to another app client in the pool is refused |
| `aws:RequestTag/customer` = B | The customer session tag that Cognito put in the token must be B. A token for Customer A is refused |
| `sts:TagSession` allowed | Required, because the token carries session tags |

The role's maximum session duration is the AWS minimum, 1 hour. ClaimClarity always requests 900 seconds.

### Reader role policy (gate 6, IAM part)

| Action | Resource | Condition |
|---|---|---|
| `s3:ListBucket` | the ClaimClarity bucket | `s3:prefix` like `B/semantic_layer/*` |
| `s3:GetObject` | `B/semantic_layer/*` | |

Nothing else. In particular:

- no `s3:GetObjectVersion`: only the current S3 object version can be read;
- no raw, silver (`parquet/`), staging, quarantine or transcript prefixes;
- no write or delete.

The escalation tables (`lossless.*`) are PHI-stripped copies kept under the gold prefix, so even an escalated
session never reads a silver object. See [SQL guard & escalation](44-sql-guard-escalation.md).

### Bucket policy and key policy (gate 6, the other two parts)

The bucket policy is the same for every customer. It denies:

- any request without TLS;
- any principal with no `customer` tag;
- object access outside `${aws:PrincipalTag/customer}/`;
- listing outside that prefix.

Customer B's key policy allows `kms:Decrypt` only to B's lake roles, only with `kms:ViaService` = S3, and only
when the encryption context `aws:s3:arn` is under `B/`. See [Encryption & keys](52-encryption-keys.md).

### Limits and timeouts

| Item | Value |
|---|---|
| Credential lifetime | 900 s (15 minutes) |
| Renewal | When fewer than 180 s remain, before the next SQL call. Same id token, same gold version |
| Id token lifetime | 3600 s. No renewal after it expires |
| Signing-key cache | 24 hours. On an unknown key id, refetch once (at most every 5 minutes), then refuse |
| Reader session | 2 GB memory, no temp directory, configuration locked |

### Failure behaviour

| Failure | Result |
|---|---|
| Gate 4 fails | 401 `auth_expired`. No run is queued |
| Gate 5 fails (`AccessDenied`, `InvalidIdentityToken`, `ExpiredTokenException`) | The run ends with a sign-in-again message. The denied call is in CloudTrail |
| Gate 6 fails (`AccessDenied` from S3 or KMS) | The SQL call errors. The agent audit records `outcome=error` |
| Credentials expired at a SQL call | The call is refused and audited as `expired`. Renewal normally prevents this |
| STS throttled or unreachable | Retried 3 times with backoff over about 5 s, then the run fails cleanly |

### Logging

CloudTrail records each AssumeRoleWithWebIdentity call, allowed or denied, with the role and session name. It also
records each S3 GetObject (data events) and each KMS Decrypt. The agent audit records the session opening
(customer, user, gold version, escalation flag, a hash of the token, never the token) and every SQL call. See
[Audit & logging](53-audit-logging.md).

## Related

Up: [Identity & access](20-identity.md) · Previous: [Sign-in](21-sign-in.md) · Next:
[Customer isolation](23-customer-isolation.md) · See also: [IAM and KMS matrix](91-iam-kms-matrix.md)
