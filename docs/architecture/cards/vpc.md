### Amazon VPC (including VPC endpoints)
| | |
|---|---|
| Purpose in ClaimClarity | Private network for the internal ALB and all ECS tasks. Two Availability Zones. No task has a public IP or a route to the internet gateway |
| Talks to | CloudFront (VPC origin to the internal ALB), AWS services through endpoints, Cognito through the single filtered egress |
| Permissions | Security groups per tier: ALB (443 from CloudFront's origin-facing prefix list), chat API (from the ALB only), run workers and parser (no inbound), endpoints (443 from the three task groups). Endpoint policies limit each endpoint to ClaimClarity resources |
| Encryption | All traffic is TLS. Endpoints keep AWS traffic on the AWS network |
| Data held / PHI | None at rest. PHI passes between tasks and S3 through the gateway endpoint |
| Scaling and limits | Endpoints: S3 (gateway); STS, KMS, Bedrock runtime, ECR api, ECR dkr, CloudWatch Logs, SQS, Secrets Manager (interface, private DNS on, one network interface per AZ). Egress: one NAT gateway per AZ behind AWS Network Firewall |
| Failure behaviour | An endpoint is per AZ, so one AZ failing leaves the other working. If the egress path fails, signed-in users continue on the cached Cognito keys; new sign-ins fail |
| Logging and audit | VPC flow logs for every subnet, Network Firewall alert and flow logs, in CloudWatch Logs for 1 year |
| Used on pages | [Network & security](50-network-security.md) · [Network](51-network.md) |
