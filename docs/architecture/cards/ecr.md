### Amazon ECR
| | |
|---|---|
| Purpose in ClaimClarity | Stores the container images for the chat API, the run worker and the parser |
| Talks to | CI (push, through the deploy role). ECS task execution roles (pull). Accessed through VPC endpoints from the private subnets. |
| Permissions | Only the deploy role may push, and only to these three repositories. Each task execution role may pull only its own repository. Nobody may delete a tagged image except the lifecycle policy. |
| Encryption | KMS encryption at rest. TLS in transit. |
| Data held / PHI | Code and dependencies only. No PHI, no secrets, no customer data, and no model credentials in any image. |
| Scaling and limits | Immutable tags (the commit SHA). Task definitions pin the image digest. Scan on push, and a critical finding stops the deploy. The lifecycle policy keeps the last 50 images per repository, enough to roll back. |
| Failure behaviour | If ECR cannot be reached, new tasks cannot start, but running tasks keep running. The ECS circuit breaker rolls back a deploy whose tasks cannot pull. |
| Logging and audit | Push and pull calls are in CloudTrail. Scan findings are reported to the security team. |
| Used on pages | [Operations](../pages/60-operations.md), [Deploy](../pages/61-deploy.md) |
