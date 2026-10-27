# Network & security

[Overview](00-overview.md) › Network & security

ClaimClarity has one public entry point, CloudFront with AWS WAF. Every ECS task runs in private subnets with no
public IP address, and reaches AWS services only through VPC endpoints. Every object is encrypted with a key that
belongs to one customer, every AWS call is recorded in CloudTrail, and every SQL call the analyzer makes is written to
a hash-chained audit record.

![Network & security](../png/50-network-security.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| 1 | XiFin users | Reach ClaimClarity over HTTPS only | [Edge](31-edge.md) |
| 2 | CloudFront + WAF | The only public entry. Serves the web app and forwards `/api/*` to the load balancer | [Network](51-network.md) |
| 3 | Internal ALB | Load balancer in the private subnets. Accepts traffic from CloudFront only | [Network](51-network.md) |
| 4 | ECS tasks on Fargate | Chat API, run workers and parser. Private subnets, no public IPs | [Network](51-network.md) |
| 5 | VPC endpoints | S3 gateway endpoint and interface endpoints for STS, KMS, Bedrock and the rest | [Network](51-network.md) |
| 6 | ClaimClarity bucket | One bucket, one prefix per customer, reached through the S3 gateway endpoint | [S3 layout](12-s3-layout.md) |
| 7 | AWS KMS | One key per customer for its lake, a second for its transcripts, and a query-audit key. TLS is required for every request | [Encryption & keys](52-encryption-keys.md) |
| 8 | CloudTrail + audit | CloudTrail for every AWS call, the agent audit for every SQL call, PHI-redacted application logs | [Audit & logging](53-audit-logging.md) |

## How it works

1. A user's browser (1) connects to CloudFront (2) over HTTPS. WAF rules run first: managed rule sets and rate
   limits per client.
2. CloudFront serves the static web app and sends `/api/*` to the internal ALB (3) through a CloudFront VPC origin.
   The ALB has no public address.
3. The ALB forwards to the chat API tasks (4). Run workers and the parser receive no inbound traffic at all.
4. Tasks call AWS only through VPC endpoints (5). S3 traffic to the ClaimClarity bucket (6) goes through the gateway
   endpoint. STS, KMS, Bedrock, ECR, CloudWatch Logs, SQS and Secrets Manager go through interface endpoints.
5. The one route to the internet is for the chat API to reach Amazon Cognito: its signing keys and its token
   endpoint. It goes through a NAT gateway and AWS Network Firewall, which allow only the Cognito host names.
6. Every object is written with SSE-KMS under its customer's key (7). The bucket policy refuses any request without
   TLS.
7. CloudTrail, the agent audit and the application logs (8) record what happened, without recording PHI in the
   application logs.

## Specification

| Control | Design |
|---|---|
| Public entry | CloudFront + WAF only. No public load balancer, no public IPs on tasks |
| Compute placement | All ECS tasks on Fargate in private subnets, in two Availability Zones |
| AWS access | S3 gateway endpoint; interface endpoints for STS, KMS, Bedrock runtime, ECR (api and dkr), CloudWatch Logs, SQS, Secrets Manager |
| Internet egress | Chat API only, to the Cognito host names, through NAT + Network Firewall |
| Encryption in transit | TLS 1.2 or later from browser to CloudFront, CloudFront to ALB and ALB to tasks. All AWS calls use HTTPS. The bucket denies non-TLS requests |
| Encryption at rest | SSE-KMS on every object, with the customer's own key. CloudWatch Logs and SQS also use KMS keys |
| Audit | CloudTrail management and S3 data events; agent audit per SQL call; PHI-redacted application logs |
| Retention | Raw files 7 years; query audit 6 years; CloudTrail 6 years; application logs 1 year; transcripts 90 days |

## Pages in this area

- [Network](51-network.md): subnets, security groups, VPC endpoints and the single egress.
- [Encryption & keys](52-encryption-keys.md): per-customer keys, key policies, TLS-only bucket and who may decrypt what.
- [Audit & logging](53-audit-logging.md): CloudTrail, the hash-chained agent audit, redacted logs and retention.

## Related

Up: [Overview](00-overview.md) · See also: [Identity & access](20-identity.md) · [Edge](31-edge.md) ·
[Monitoring](62-monitoring.md) · [IAM and KMS matrix](91-iam-kms-matrix.md)
