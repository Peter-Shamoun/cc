### Elastic Load Balancing (Application Load Balancer)
| | |
|---|---|
| Purpose in ClaimClarity | Spreads `/api/*` traffic from CloudFront across the chat API tasks, and takes unhealthy tasks out of service |
| Talks to | CloudFront (inbound, HTTPS). The chat API target group (ECS on Fargate, private subnets). |
| Permissions | Its security group allows inbound HTTPS only from the CloudFront origin-facing managed prefix list. A listener rule also requires the secret origin header that CloudFront adds. Its outbound traffic may reach only the chat API tasks' port. |
| Encryption | HTTPS listener with an ACM certificate, and HTTPS (or TLS) to the targets. |
| Data held / PHI | None stored. Requests and SSE responses, which can hold PHI, pass through. |
| Scaling and limits | Idle timeout 60 s, which is above the 15 s SSE heartbeat. Health check `GET /api/health`, which must return 200. Deregistration delay 120 s, so open streams can drain during a deploy. Stickiness is on (load-balancer cookie, 1 hour), only so a temporary chat, which lives in one task's memory, keeps its follow-ups. Saved chats do not depend on it: their run state is in S3, so any task can serve them. |
| Failure behaviour | A failing target is removed. If a stream is cut during a drain, the browser reconnects with `Last-Event-ID` and replays from the stored run events. |
| Logging and audit | Access logs go to a separate log bucket (they show full paths with opaque ids: PHI-bearing, encrypted, 90 days). CloudWatch metrics: 5xx count, response time, healthy hosts. |
| Used on pages | [Edge](../pages/31-edge.md), [Monitoring](../pages/62-monitoring.md) |
