# Parse trigger

[Overview](00-overview.md) › [Ingestion & lake](10-ingestion.md) › Parse trigger

When a customer's day folder is complete, its `_SUCCESS` marker starts exactly one parser task for that
customer. The chain uses only AWS-managed services: S3 sends the event, an EventBridge rule per customer
matches it, and ECS starts a Fargate task from that customer's task definition. The launcher can start tasks
but can never read data, and each task runs as one customer's role only.

![Parse trigger](../png/13-parse-trigger.png)

## In this diagram

| # | Item | What it does | Details |
|---|---|---|---|
| 1 | `_SUCCESS` | Marker object written last into Customer B's day folder | [SFTP intake](11-sftp-intake.md) |
| 2 | EventBridge rule | Customer B's rule. Matches only `_SUCCESS` objects under Customer B's raw prefix. | [EventBridge card](90-service-catalog.md) |
| 3 | Amazon ECS | Runs RunTask on the ClaimClarity cluster with the Fargate launch type | [ECS and Fargate card](90-service-catalog.md) |
| 4 | Parser task | One container for one batch | [Parser task](14-parser-task.md) |
| 5 | Dead-letter queue | Amazon SQS queue for events the rule could not deliver | |
| 6 | Launcher role | The rule's target role. Starts parser tasks and passes their roles. | |
| 7 | Task execution role | Used by ECS, not by the code: pulls the image, writes container logs | |
| 8 | Parser role | Customer B's task role. The only credentials the code inside the task gets. | [Parser task](14-parser-task.md) |

## How it works

1. The `_SUCCESS` object (1) lands in `<customer>/raw/received_date=YYYY-MM-DD/`. The bucket sends every
   Object Created event to EventBridge.
2. Customer B's rule (2) matches the event. **Gate 1:** the rule's pattern names the ClaimClarity bucket and
   the key `customer_b/raw/received_date=*/_SUCCESS`, so another customer's upload never matches it.
3. The rule calls ECS RunTask (3) as the launcher role (6). It passes the marker's key as `SUCCESS_KEY` and the
   event id as `EVENT_ID` in the container's environment.
4. **Gate 2:** ECS checks that the launcher may run this task definition on this cluster and may pass
   Customer B's parser role (8) to ECS tasks.
5. ECS starts the parser task (4) on Fargate. It pulls the image with the task execution role (7) and gives
   the container Customer B's parser role.
6. If the rule cannot start the task after 3 attempts, the event goes to the dead-letter queue (5).

## Specification

### Rule and target

| Item | Value |
|---|---|
| Event source | S3 "Object Created", with EventBridge notifications turned on for the ClaimClarity bucket |
| Rule | One per customer. Pattern: `source = aws.s3`, `detail-type = Object Created`, bucket = the ClaimClarity bucket, key matches `<customer>/raw/received_date=*/_SUCCESS` |
| Target | ECS RunTask on the ClaimClarity cluster, launch type Fargate, task definition = that customer's parser task definition |
| Input passed | `SUCCESS_KEY` (the marker's key) and `EVENT_ID` (the event id), as container environment overrides |
| Fixed in the task definition | `CUSTOMER`, `BUCKET`, `KMS_KEY_ARN` (the customer's key), pipeline settings (`GOLD_MODE`, `PARSE_WORKERS`) |
| Network | Private subnets, no public IP. See [Network](51-network.md). |

### Permissions

| Role | Trusted by | Allowed |
|---|---|---|
| Launcher role | EventBridge, this account only | `ecs:RunTask` on the parser task definitions, only on the ClaimClarity cluster; `iam:PassRole` on the parser roles and the task execution role, only to `ecs-tasks.amazonaws.com` |
| Task execution role | ECS tasks | Pull the parser image from ECR; write container logs to CloudWatch Logs |
| Customer B's parser role | ECS tasks of this account only | Customer B's prefix only (see [Parser task](14-parser-task.md)); maximum session 1 hour |

The launcher holds no S3 or KMS permission. It can start a task but cannot read what the task reads.

### Failure behaviour and retries

| Event | What happens |
|---|---|
| RunTask fails (throttling, capacity, a denied PassRole) | EventBridge retries. After 3 attempts the event goes to the dead-letter queue. |
| The same event is delivered twice | Two tasks may start. The batch lease runs them one after the other; the second finds every file already loaded and exits with status `noop`. |
| The task starts but fails | RunTask succeeded, so EventBridge does not retry. The task stops with a non-zero exit code and its reason ([Monitoring](62-monitoring.md)). The day is loaded by a re-run. |
| Re-running a day | Start the customer's task definition with the same `SUCCESS_KEY`, or redrive the event from the dead-letter queue. Re-runs are safe: loaded files are skipped. |

### Logging

CloudTrail records RunTask, PassRole and failed target invocations, allowed or denied. The task's
`triggered_by` (task ARN) and `EVENT_ID` are stored in the batch's `edi.ingest_run` row, so every silver row can
be traced to the event that started it.

### Credentials after the task ends

ECS task-role credentials are not revoked when the task stops. They expire on their own schedule (up to about
6 hours). The PHI boundary is the destroyed container and its disk, not the credentials.

## Related

Up: [Ingestion & lake](10-ingestion.md) · Previous: [SFTP intake](11-sftp-intake.md) · Next:
[Parser task](14-parser-task.md)
