# Customer isolation

[Overview](00-overview.md) › [Identity & access](20-identity.md) › Customer isolation

Customer A's data and Customer B's data sit in the same ClaimClarity bucket. They are kept apart by several
independent layers, and each layer refuses on its own. A mistake in one layer, such as a wrong IAM policy, still
leaves the others closed. This page follows a signed-in Customer A user through every way they could try to reach
Customer B's data.

![Customer isolation](../png/23-customer-isolation.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | A's user | A signed-in Customer A user whose request names Customer B (in the body, the URL or a header) | |
| 2 | Chat API refuses | Takes the customer from the verified token only (gate 4). The request's own value is ignored | [Token to credentials](22-token-to-credentials.md) |
| 3 | A's id token | Carries `custom:customer = A` and the session tag `customer = A`, signed by Cognito | [Sign-in](21-sign-in.md) |
| 4 | STS refuses | Customer B's reader role trusts only tokens tagged `customer = B` (gate 5) | [Token to credentials](22-token-to-credentials.md) |
| 5 | A's reader role | The 15-minute credentials of Customer A's reader role | |
| 6 | S3 and KMS refuse | A's role policy, the bucket policy and Customer B's key policy each deny the read (gate 6) | [Encryption & keys](52-encryption-keys.md) |
| 7 | Agent SQL | SQL that the custom analysis agent writes, naming a file path under B's prefix | [SQL guard & escalation](44-sql-guard-escalation.md) |
| 8 | Reader session refuses | The SQL guard rejects file functions, and DuckDB's allowed directories cover A's gold relations only | [SQL guard & escalation](44-sql-guard-escalation.md) |

## How it works

Each lane is one way to try. Each would fail even if the lanes above it had let the request through.

1. **Name Customer B in the request (1, 2).** The API reads the customer from the verified id token's
   `custom:customer` claim (gate 4). Nothing else can set the customer: not the body, the query string or a header.
   Conversations are keyed by customer and `sub`, so A's user cannot list or open B's conversations either.
2. **Assume Customer B's role (3, 4).** The worker could only ask STS for B's reader role if the token named B. Even
   if it did, B's trust policy requires `aws:RequestTag/customer = B`. Cognito writes that tag from the
   administrator-set attribute, and the user cannot change it. A forged token with `customer = B` fails the
   signature check, and so does a token with `alg=none` (gate 5).
3. **Read Customer B's files with A's credentials (5, 6).** Three separate policies refuse (gate 6):
   - A's reader role allows `GetObject` on `A/semantic_layer/*` only;
   - the bucket policy denies any object access outside `${aws:PrincipalTag/customer}/`, which is `A/`;
   - Customer B's key policy allows decryption only to B's roles, through S3, for objects under `B/`.
   So even a wrongly broad role policy cannot read B's bytes: the bucket policy and B's key still refuse.
4. **Send SQL that points at B's files (7, 8).** The reader session refuses:
   - the SQL guard rejects `read_parquet`, globs, `COPY`, `ATTACH`, `SET` and every other file or network function
     before DuckDB runs;
   - DuckDB's `allowed_directories` lists only A's gold relation prefixes;
   - external access is off and the configuration is locked;
   - each DuckDB secret is scoped to one of A's relation prefixes, and the credentials behind it are A's.
   Path tricks such as `..`, `%2e%2e` or a lookalike prefix (`A_extra/`) do not reach B's `CURRENT` pointer.

## Specification

### Layers at a glance

| Layer | What it checks | Where it is set | Refusal |
|---|---|---|---|
| Entra ID | User is in a group assigned to the ClaimClarity app | Entra enterprise app | AADSTS50105 |
| Cognito | User has a customer mapping (`custom:customer`) | Cognito user attribute, administrator only | No token issued |
| Chat API (gate 4) | Token signature, issuer, audience, expiry, customer claim | API configuration | 401 |
| STS (gate 5) | Trust policy: Cognito provider, app client, `customer` tag | Each reader role's trust policy | `AccessDenied` |
| IAM role policy (gate 6) | Reader reads `<customer>/semantic_layer/*` only, current version | Each reader role's policy | `AccessDenied` |
| Bucket policy (gate 6) | Object access only under the caller's `customer` tag | One bucket policy for all customers | `AccessDenied` |
| KMS key policy (gate 6) | Decrypt only by that customer's roles, via S3, under its prefix | Each customer's key policy | `AccessDenied` |
| Reader session | SQL guard, allowed directories, scoped secrets, locked config | Chosen when the session opens | Guard error |
| Chat state | Conversations keyed by customer and `sub` | Chat API | 404 |

### What an administrator controls

- Mapping a user to a customer: setting `custom:customer` on the Cognito user. Users cannot write the attribute.
- Who may sign in at all: membership of the assigned Entra groups.
- Taking a customer offline at once: disabling Customer B's key. That makes every B object unreadable for everyone
  ("crypto-shredding"). A key administrator can disable the key but cannot decrypt with it.

### Not covered by these layers

- Inside one customer, the gold tables are a limited data set, not de-identified data. Small-group results are
  suppressed only for rate metrics. See [SQL guard & escalation](44-sql-guard-escalation.md).
- A stolen id token works until it expires (60 minutes). To cut it off sooner, revoke active sessions on the
  customer's reader role ([Sign-in](21-sign-in.md)).

### Logging

Every refusal at gates 5 and 6 appears in CloudTrail with `errorCode=AccessDenied`, the role and the session name.
Guard refusals appear in the agent audit as `outcome=blocked`. A CloudWatch alarm on cross-customer denials is
described in [Audit & logging](53-audit-logging.md).

## Related

Up: [Identity & access](20-identity.md) · Previous: [Token to credentials](22-token-to-credentials.md) · See also:
[Encryption & keys](52-encryption-keys.md) · [IAM and KMS matrix](91-iam-kms-matrix.md)
