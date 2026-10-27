# Operations

[Overview](00-overview.md) › Operations

Every change ships through Git, CI and ECR to ECS, and analyzer changes must also keep their accuracy. CloudWatch
watches the running tasks and pages on-call when an alarm fires.

![Operations](../png/60-operations.png)

## In this diagram

| Item | What it does | Details |
|---|---|---|
| Git | The repository. Changes come in as reviewed pull requests. | [Deploy](61-deploy.md) |
| CI | Builds and tests every change, including the eval gate for analyzer changes | [Deploy](61-deploy.md) |
| Amazon ECR | Holds the chat API, run worker and parser images | [Deploy](61-deploy.md) |
| ECS services | Run the new images on Fargate after the health checks pass | [Deploy](61-deploy.md) |
| ECS tasks | Write redacted logs and metrics | [Monitoring](62-monitoring.md) |
| CloudWatch | Logs, metrics and alarms | [Monitoring](62-monitoring.md) |
| Alarm → on-call | Notifies on-call | [Monitoring](62-monitoring.md) |
| Thumbs-down + sampled chats | Become candidate test questions | [Eval regression](63-eval-regression.md) |
| Regression set | Questions with answer keys, re-run on every analyzer change | [Eval regression](63-eval-regression.md) |
| Ship only if accuracy holds | Blocks an analyzer change that is less accurate | [Eval regression](63-eval-regression.md) |

## How it works

1. A pull request runs CI. Analyzer changes also run the regression set.
2. On merge, CI pushes images to ECR, and the deploy step rolls the ECS services.
3. The tasks log to CloudWatch, and alarms notify on-call.
4. Thumbs-down answers and sampled chats keep the regression set growing.

## Specification

| Item | Value |
|---|---|
| Deploy identity | GitHub Actions assumes the deploy role through OIDC. No AWS keys are stored in GitHub. |
| Deploy role | Cannot read customer data, use the customer's keys, or change bucket or key policies |
| Logs | No question text, SQL or row values in any log. App logs are kept 1 year. |

## Pages in this area

- [Deploy](61-deploy.md) · [Monitoring](62-monitoring.md) · [Eval regression](63-eval-regression.md)

## Related

Up: [Overview](00-overview.md) · See also: [Chat backend](30-chat-backend.md), [Audit & logging](53-audit-logging.md)
