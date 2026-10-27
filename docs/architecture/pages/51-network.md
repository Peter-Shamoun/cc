# Network

[Overview](00-overview.md) › [Network & security](50-network-security.md) › Network

The ClaimClarity VPC has no internet-facing resources. Users come in through CloudFront and WAF only. ECS tasks have
no public IPs and talk to AWS through VPC endpoints, so PHI never crosses the public internet inside AWS. One
filtered egress path exists, and it serves one purpose: the chat API reaching Amazon Cognito.

![Network](../png/51-network.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | Browser | Connects over HTTPS only | [Edge](31-edge.md) |
| 2 | CloudFront + WAF | Public entry. Serves the web app from S3 and sends `/api/*` to the internal ALB through a VPC origin | [Edge](31-edge.md) |
| 3 | Internal ALB | In the private subnets. Its security group admits CloudFront's origin-facing addresses only | |
| 4 | ECS tasks | Chat API, run workers and parser on Fargate. No public IPs. One security group per tier | [Chat backend](30-chat-backend.md) |
| 5 | VPC endpoints | S3 gateway endpoint plus eight interface endpoints, with private DNS on | |
| 6 | AWS services | STS, KMS, Bedrock runtime, ECR, CloudWatch Logs, SQS, Secrets Manager, reached through interface endpoints | |
| 7 | ClaimClarity bucket | Reached through the S3 gateway endpoint, never through the NAT | [S3 layout](12-s3-layout.md) |
| 8 | NAT + Network Firewall | The only egress. Allows HTTPS to the Cognito host names and nothing else | |
| 9 | Amazon Cognito | The user pool's signing keys (JWKS) and the token endpoint used at sign-in | [Sign-in](21-sign-in.md) |

## How it works

1. The browser (1) connects to CloudFront (2). WAF applies AWS managed rule sets and rate limits.
2. CloudFront forwards `/api/*` to the internal ALB (3) through a CloudFront VPC origin. The ALB has no public
   address, and its security group admits only CloudFront's origin-facing managed prefix list.
3. The ALB forwards HTTPS to the chat API tasks (4). Run workers pull work from SQS and the parser is started by
   EventBridge, so neither accepts inbound connections.
4. Tasks call AWS through the VPC endpoints (5). Interface endpoints carry STS, KMS, Bedrock, ECR, Logs, SQS and
   Secrets Manager (6). The S3 gateway endpoint carries all S3 traffic, including ECR image layers and the
   ClaimClarity bucket (7).
5. The chat API reaches Cognito (9) through the NAT gateway and Network Firewall (8). It fetches the signing keys at
   start and caches them for 24 hours. It refetches early only for an unknown key id, at most once every 5 minutes.
   The sign-in callback also calls the Cognito token endpoint this way. STS verifies tokens on the AWS side and
   needs no egress from the VPC.

## Specification

### Subnets and routing

| Subnet | Contents | Default route |
|---|---|---|
| Private (one per AZ, two AZs) | Internal ALB, chat API, run workers, parser, interface endpoint network interfaces | NAT gateway in the egress subnet; S3 prefix list to the gateway endpoint |
| Egress (one per AZ) | Network Firewall endpoint and NAT gateway | Internet gateway |

No task subnet has a route to the internet gateway. ECS services and the parser task definition set
`assignPublicIp = DISABLED`.

### Security groups (one per tier)

| Group | Inbound | Outbound |
|---|---|---|
| ALB | 443 from the CloudFront origin-facing prefix list | App port to the chat API group |
| Chat API | App port from the ALB group | 443 to the endpoint group; 443 to the S3 prefix list; 443 to the internet (filtered by Network Firewall to Cognito) |
| Run workers | none | 443 to the endpoint group; 443 to the S3 prefix list |
| Parser | none | 443 to the endpoint group; 443 to the S3 prefix list |
| Endpoints | 443 from the chat API, run worker and parser groups | none |

Run workers and the parser have no internet path: only the chat API group has an outbound rule beyond the
endpoints, and the firewall limits even that.

### VPC endpoints

| Endpoint | Type | Used by | Endpoint policy |
|---|---|---|---|
| S3 | Gateway | All tasks, ECR layer pulls | The ClaimClarity bucket and the ECR layer bucket only |
| STS | Interface | Run workers | AssumeRoleWithWebIdentity |
| KMS | Interface | Tasks that encrypt outside S3 (SQS messages, transcript data keys) | ClaimClarity keys only |
| Bedrock runtime | Interface | Run workers | InvokeModel on Claude Haiku only |
| ECR api, ECR dkr | Interface | Fargate image pulls | ClaimClarity repositories only |
| CloudWatch Logs | Interface | All tasks | ClaimClarity log groups only |
| SQS | Interface | Chat API, run workers | The run queue only |
| Secrets Manager | Interface | Chat API | The ClaimClarity secrets only |

Interface endpoints have private DNS on, so the standard AWS host names resolve to the endpoints inside the VPC.
The bucket policy can also deny reads that do not arrive through the ClaimClarity S3 endpoint (`aws:SourceVpce`),
with exceptions for the SFTP copy role and the deploy role.

### Egress filter

| Allowed host | Why |
|---|---|
| The Cognito user pool's issuer host | Signing keys (`/.well-known/jwks.json`) |
| The Cognito user pool's sign-in domain | Token endpoint at sign-in (code exchange) |

Everything else is dropped and logged by Network Firewall. If the egress path fails, users who are already signed in
keep working while the cached keys last. New sign-ins fail until it is back.

### TLS

| Hop | Protocol |
|---|---|
| Browser to CloudFront | HTTPS, TLS 1.2 or later, HSTS (2 years, includeSubDomains) |
| CloudFront to ALB | HTTPS through the VPC origin |
| ALB to chat API | HTTPS |
| Tasks to AWS | HTTPS through endpoints, SigV4. The bucket denies non-TLS requests |

### Logging

VPC flow logs for every subnet, Network Firewall alert and flow logs, ALB access logs and WAF logs go to CloudWatch
Logs, encrypted with KMS, kept 1 year. See [Audit & logging](53-audit-logging.md).

## Related

Up: [Network & security](50-network-security.md) · See also: [Edge](31-edge.md) · [Encryption & keys](52-encryption-keys.md) ·
[VPC card](90-service-catalog.md)
