# Deploy

[Overview](00-overview.md) › [Operations](60-operations.md) › Deploy

One repository feeds three release lanes: container images, the web app and the gold view SQL. Each lane is
built and tested in CI from a reviewed pull request, then deployed by a pipeline role that no person uses
day to day. Images are built once and promoted by digest. ECS replaces tasks only after the new ones pass
their health checks, so a bad build never takes traffic.

![Deploy](../png/61-deploy.png)

## In this diagram

| # | Box | What it does | Details |
|---|---|---|---|
| – | GitHub | The repository. `main` is protected: a reviewed pull request and green CI are required. | |
| 1 | CI build and test | Lint, type checks, unit, contract and security tests, the eval gate for analyzer changes, then the image builds | [Eval regression](63-eval-regression.md) |
| 2 | Amazon ECR | One repository per image: chat API, run worker, parser. Immutable tags, scan on push. | [ECR card](../cards/ecr.md) |
| 3 | Amazon ECS | Rolling deploy of the chat API and run worker services. A new task definition revision for the parser. | [ECS card](../cards/ecs-fargate.md) |
| 4 | Web build | Vite production build with Subresource Integrity hashes and a nonce placeholder in the HTML | [Edge](31-edge.md) |
| 5 | Web app bucket | New hashed assets are uploaded first, then the `index.html` template | [Edge](31-edge.md) |
| 6 | CloudFront | An invalidation of the HTML paths only | [CloudFront card](../cards/cloudfront.md) |
| 7 | View SQL | The gold (`sem.*`) view definitions, versioned in git with the parser code | [Gold publish](15-gold-publish.md) |
| 8 | Gold publish | The parser task built from that commit publishes the views with the next batch | [Gold publish](15-gold-publish.md) |

## How it works

**Images**

1. CI runs on every pull request that touches the code. All of these must pass:
   - API tests (`pytest`): the contract suite (every emitted event is checked against the JSON schema and the
     stream ordering rules), the security header tests and the deploy-checklist test, which fails if an
     environment variable is undocumented.
   - Web checks: licence audit, lint with zero warnings, type check, unit tests (including the PHI-sink test
     that streams every fixture and searches console, storage, URL and title for row values), production
     build, bundle budget, SRI check, CSP check and header check against a preview build, Lighthouse budget.
   - Contract drift: the TypeScript types are generated from the schema, and the build fails on drift.
   - The eval gate for any change to the analyzer, its prompts or its knowledge files
     ([Eval regression](63-eval-regression.md)).
2. On merge, CI builds the three images once, tags them with the commit SHA and pushes them to ECR. ECR scans
   each image on push. A critical finding stops the pipeline.
3. The deploy step registers new task definitions that pin the image digest and updates the services:
   - Chat API: rolling, at least 100 % healthy and at most 200 %. A new task joins the load balancer target
     group only after `GET /api/health` returns 200. Old tasks drain their open SSE streams for up to 120 s.
     The browser reconnects any stream that is cut, and replays from the stored run events.
   - Run workers: rolling. A stopping worker stops receiving from the queue and finishes its current run. The
     run deadline (110 s) fits inside the 120 s stop timeout.
   - Parser: a new revision of the task definition. The launcher starts the latest revision for the next batch.
4. The ECS deployment circuit breaker rolls back to the previous revision if new tasks fail to start or fail
   their health checks.

**Web app**

5. CI builds the web app with cookie sign-in and the sign-in URL. A build that contains a demo token fails.
6. The deploy step uploads the new `/assets/*` files first (they never overwrite an existing name), then the
   `index.html` template. Old assets stay for at least one cache lifetime, so open tabs can still load lazy chunks.
7. It invalidates `/` and `/index.html` in CloudFront. After the deploy it runs the SRI, header and CSP checks
   against the live site.

**View SQL**

8. A change to a gold view is a pull request to the view SQL. CI runs it against the synthetic test data set and
   checks every gold answer against its independent answer key. The view SQL ships inside the parser image.
   The next parser run publishes the views under a new version and moves the CURRENT pointer
   ([Gold publish](15-gold-publish.md)).

## Specification

| Item | Value |
|---|---|
| CI identity | GitHub Actions assumes the deploy role through OIDC, limited to the protected branch. No AWS keys are stored in GitHub. |
| Deploy role can | Push to the three ECR repositories, register task definitions, update the two ECS services, pass the task and execution roles (iam:PassRole on those roles only), put objects in the web app bucket, create CloudFront invalidations |
| Deploy role cannot | Read the ClaimClarity bucket, use any customer key, or change bucket and key policies. Those belong to the infrastructure pipeline. |
| Image tags | Commit SHA, immutable. Task definitions pin the digest. |
| Rollback | ECS circuit breaker, or redeploy the previous task definition revision. Web app: upload the previous `index.html` template and invalidate again (the old assets are still there). |
| Secrets | None in images. Runtime settings come from the task definition. Secrets, if any, come from Secrets Manager. |
| Production settings | Cookie auth with an explicit backend, the paid-model switch only in production, SQL literal masking `phi` or `all` (never `off`), heartbeat 15 s |

## Related

Up: [Operations](60-operations.md) · See also: [Monitoring](62-monitoring.md),
[Eval regression](63-eval-regression.md), [Parser task](14-parser-task.md), cards: [ECR](../cards/ecr.md),
[ECS on Fargate](../cards/ecs-fargate.md)
